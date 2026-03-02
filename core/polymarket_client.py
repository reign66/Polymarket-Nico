"""
core/polymarket_client.py

HTTP client for the Polymarket Gamma API and CLOB API.
Handles paper-trading simulation via the local DB when PAPER_TRADING=true.
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests

from core.database import (
    Session,
    Trade,
    close_position,
    get_capital,
    get_open_positions,
    open_position,
)

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

_REQUEST_TIMEOUT = 10  # seconds
_MAX_RETRIES = 3
_RETRY_BACKOFF = [1, 2, 4]  # seconds between retries


class PolymarketClient:
    """
    Unified client for Polymarket market data and order placement.

    Parameters
    ----------
    db_session : sqlalchemy.orm.Session
        Active database session used for paper-trading persistence.
    paper_trading : bool, optional
        Override for the PAPER_TRADING env var.  Defaults to the env var value
        (which itself defaults to True for safety).
    """

    def __init__(self, db_session: Session, paper_trading: bool = None) -> None:
        self.session = db_session

        if paper_trading is not None:
            self.paper_trading = paper_trading
        else:
            env_val = os.environ.get("PAPER_TRADING", "true").strip().lower()
            self.paper_trading = env_val not in ("false", "0", "no")

        mode = "PAPER" if self.paper_trading else "REAL"
        logger.info("PolymarketClient initialised in %s trading mode.", mode)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request(
        self,
        url: str,
        method: str = "GET",
        params: dict = None,
        json_data: dict = None,
        auth: bool = False,
    ):
        """
        Execute an HTTP request with retry logic.

        Returns the parsed JSON body (dict or list) on success, or None on
        permanent failure.  Never raises an exception to the caller.
        """
        headers = {"Content-Type": "application/json", "Accept": "application/json"}

        if auth:
            api_key = os.environ.get("POLYMARKET_API_KEY", "")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

        for attempt, backoff in enumerate(_RETRY_BACKOFF, start=1):
            try:
                response = requests.request(
                    method=method.upper(),
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_data,
                    timeout=_REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                return response.json()

            except requests.exceptions.Timeout:
                logger.warning(
                    "Request timeout (attempt %d/%d): %s", attempt, _MAX_RETRIES, url
                )
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "?"
                logger.warning(
                    "HTTP %s (attempt %d/%d): %s", status, attempt, _MAX_RETRIES, url
                )
                # 4xx errors are not worth retrying
                if exc.response is not None and 400 <= exc.response.status_code < 500:
                    logger.error("Client error %s for %s — aborting retries.", status, url)
                    return None
            except requests.exceptions.RequestException as exc:
                logger.warning(
                    "Request error (attempt %d/%d): %s — %s", attempt, _MAX_RETRIES, url, exc
                )
            except (json.JSONDecodeError, ValueError) as exc:
                logger.error("JSON decode error for %s: %s", url, exc)
                return None

            if attempt < _MAX_RETRIES:
                time.sleep(backoff)

        logger.error("All %d attempts failed for %s", _MAX_RETRIES, url)
        return None

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_market(raw: dict) -> Optional[dict]:
        """
        Normalise a raw Gamma API market dict into the internal schema.

        The Gamma API can represent prices in several ways:
        - ``outcomePrices`` may be a JSON string like ``'["0.65","0.35"]'``
          where index 0 = YES, index 1 = NO.
        - Individual token objects in ``tokens`` may carry a ``price`` field.
        """
        if not raw:
            return None

        try:
            market_id = raw.get("id") or raw.get("conditionId", "")
            question = raw.get("question", "")

            # --- Price extraction ---
            yes_price: Optional[float] = None
            no_price: Optional[float] = None

            outcome_prices_raw = raw.get("outcomePrices")
            if outcome_prices_raw:
                try:
                    if isinstance(outcome_prices_raw, str):
                        prices = json.loads(outcome_prices_raw)
                    else:
                        prices = list(outcome_prices_raw)
                    yes_price = float(prices[0])
                    no_price = float(prices[1])
                except (json.JSONDecodeError, IndexError, ValueError):
                    pass

            # Fallback: tokens list
            if yes_price is None:
                tokens = raw.get("tokens", [])
                for token in tokens:
                    outcome = str(token.get("outcome", "")).upper()
                    price = token.get("price")
                    if price is not None:
                        if outcome == "YES":
                            yes_price = float(price)
                        elif outcome == "NO":
                            no_price = float(price)

                # If outcomes are not labelled, use positional fallback
                if yes_price is None and len(tokens) >= 2:
                    try:
                        yes_price = float(tokens[0].get("price", 0))
                        no_price = float(tokens[1].get("price", 0))
                    except (TypeError, ValueError):
                        pass

            yes_price = yes_price or 0.0
            no_price = no_price or (1.0 - yes_price)

            # --- Spread ---
            spread = round(abs(1.0 - yes_price - no_price), 4)

            # --- Volume ---
            volume = 0.0
            for field in ("volume", "volume24hr", "volumeNum"):
                v = raw.get(field)
                if v is not None:
                    try:
                        volume = float(v)
                        break
                    except (TypeError, ValueError):
                        pass

            # --- End date ---
            end_date = raw.get("endDate") or raw.get("endDateIso") or ""

            # --- Active flag ---
            active = bool(raw.get("active", True))
            closed = bool(raw.get("closed", False))
            if closed:
                active = False

            return {
                "id": market_id,
                "question": question,
                "yes_price": round(yes_price, 4),
                "no_price": round(no_price, 4),
                "volume": volume,
                "end_date": end_date,
                "spread": spread,
                "active": active,
                "description": raw.get("description", ""),
                "outcomes": raw.get("outcomes", ["YES", "NO"]),
            }
        except Exception as exc:
            logger.error("_parse_market failed for raw id=%s: %s", raw.get("id"), exc)
            return None

    def get_active_markets(self, keywords: List[str] = None) -> List[dict]:
        """
        Fetch active markets from the Gamma API.

        Parameters
        ----------
        keywords : list of str, optional
            If provided, only markets whose ``question`` contains at least one
            keyword (case-insensitive) are returned.

        Returns
        -------
        list of dict
            Each dict has keys: id, question, yes_price, no_price, volume,
            end_date, spread, active.
        """
        params = {"active": "true", "limit": 100}
        url = f"{GAMMA_API}/markets"

        raw_list = self._request(url, params=params)
        if not raw_list:
            logger.warning("get_active_markets: empty or failed response.")
            return []

        # Gamma sometimes wraps results
        if isinstance(raw_list, dict):
            raw_list = raw_list.get("data") or raw_list.get("markets") or []

        markets = []
        for raw in raw_list:
            parsed = self._parse_market(raw)
            if parsed is None:
                continue
            if keywords:
                question_lower = parsed["question"].lower()
                if not any(kw.lower() in question_lower for kw in keywords):
                    continue
            markets.append(parsed)

        logger.info("get_active_markets: returned %d markets.", len(markets))
        return markets

    def get_market_details(self, market_id: str) -> Optional[dict]:
        """
        Fetch full details for a single market.

        Returns a dict with: id, question, yes_price, no_price, volume,
        spread, end_date, description, outcomes — or None on failure.
        """
        url = f"{GAMMA_API}/markets/{market_id}"
        raw = self._request(url)
        if not raw:
            logger.warning("get_market_details: no data for market_id=%s.", market_id)
            return None
        return self._parse_market(raw)

    # ------------------------------------------------------------------
    # Order placement (paper / real)
    # ------------------------------------------------------------------

    def place_bet(
        self,
        market_id: str,
        direction: str,
        amount_usdc: float,
        price: float,
        bot_niche: str = "unknown",
        market_question: str = "",
        signal_meta: dict = None,
    ) -> dict:
        """
        Place a bet on a market.

        Parameters
        ----------
        market_id : str
        direction : str
            ``"YES"`` or ``"NO"``.
        amount_usdc : float
            Size in USDC to commit.
        price : float
            Entry price for the direction token (0–1).
        bot_niche : str
            Label for the originating bot (stored in the position row).
        market_question : str
            Human-readable question (stored for reference).
        signal_meta : dict, optional
            Keys: haiku_score, sonnet_confidence, edge_at_entry.

        Returns
        -------
        dict
            ``{success, trade_id, paper}`` on paper success.
            ``{success, error}`` on failure.
        """
        signal_meta = signal_meta or {}
        direction = direction.upper()

        if self.paper_trading:
            return self._place_paper_bet(
                market_id, direction, amount_usdc, price,
                bot_niche, market_question, signal_meta,
            )
        else:
            return self._place_real_bet(market_id, direction, amount_usdc, price)

    def _place_paper_bet(
        self,
        market_id: str,
        direction: str,
        amount_usdc: float,
        price: float,
        bot_niche: str,
        market_question: str,
        signal_meta: dict,
    ) -> dict:
        """Create DB records for a simulated trade and open position."""
        try:
            position = open_position(
                self.session,
                bot_niche=bot_niche,
                market_id=market_id,
                market_question=market_question,
                direction=direction,
                entry_price=price,
                current_price=price,
                size_usdc=amount_usdc,
                pnl_latent=0.0,
                haiku_score=signal_meta.get("haiku_score"),
                sonnet_confidence=signal_meta.get("sonnet_confidence"),
                edge_at_entry=signal_meta.get("edge_at_entry"),
            )
            if position is None:
                return {"success": False, "error": "Failed to create position in DB"}

            trade = Trade(
                position_id=position.id,
                market_id=market_id,
                direction=direction,
                action="buy",
                price=price,
                amount_usdc=amount_usdc,
                paper_trading=True,
            )
            self.session.add(trade)
            self.session.commit()
            self.session.refresh(trade)

            logger.info(
                "Paper bet placed: market=%s dir=%s size=%.2f price=%.4f position_id=%d trade_id=%d",
                market_id, direction, amount_usdc, price, position.id, trade.id,
            )
            return {"success": True, "trade_id": trade.id, "position_id": position.id, "paper": True}

        except Exception as exc:
            logger.error("_place_paper_bet failed: %s", exc)
            try:
                self.session.rollback()
            except Exception:
                pass
            return {"success": False, "error": str(exc)}

    def _place_real_bet(
        self,
        market_id: str,
        direction: str,
        amount_usdc: float,
        price: float,
    ) -> dict:
        """
        Skeleton for live CLOB order placement.

        Full implementation requires py-clob-client with wallet credentials.
        """
        logger.warning(
            "Real trading: place_bet called for market=%s dir=%s size=%.2f. "
            "Full py-clob-client integration required — order NOT sent.",
            market_id, direction, amount_usdc,
        )
        # TODO: implement with py-clob-client
        # from py_clob_client.client import ClobClient
        # from py_clob_client.clob_types import OrderArgs, OrderType
        # client = ClobClient(host=CLOB_API, key=os.environ["PK"], chain_id=137)
        # order_args = OrderArgs(token_id=..., price=price, size=amount_usdc, side=direction)
        # signed_order = client.create_order(order_args)
        # resp = client.post_order(signed_order, OrderType.GTC)
        # return {"success": True, "order_id": resp["orderID"], "paper": False}
        return {
            "success": False,
            "error": "Real trading not fully configured. Set PAPER_TRADING=true or complete CLOB integration.",
        }

    # ------------------------------------------------------------------
    # Sell / close
    # ------------------------------------------------------------------

    def sell_position(self, position_id: int, amount_usdc: float = None) -> dict:
        """
        Close an open position.

        Fetches the current market price, then calls ``close_position`` in DB
        (paper) or submits a CLOB sell order (real).

        Returns
        -------
        dict
            ``{success, pnl, exit_price, paper}``
        """
        if self.paper_trading:
            return self._sell_paper_position(position_id, amount_usdc)
        else:
            return self._sell_real_position(position_id, amount_usdc)

    def _sell_paper_position(self, position_id: int, amount_usdc: float = None) -> dict:
        """Close a paper position at the current market price."""
        try:
            from core.database import Position

            position = self.session.query(Position).filter(
                Position.id == position_id, Position.status == "open"
            ).first()

            if position is None:
                return {"success": False, "error": f"Open position id={position_id} not found"}

            # Fetch live price
            details = self.get_market_details(position.market_id)
            if details:
                exit_price = (
                    details["yes_price"] if position.direction == "YES"
                    else details["no_price"]
                )
            else:
                # Fallback to last known price
                exit_price = position.current_price
                logger.warning(
                    "sell_position: could not fetch live price for market=%s, using current_price=%.4f",
                    position.market_id, exit_price,
                )

            closed = close_position(
                self.session,
                position_id=position_id,
                exit_price=exit_price,
                exit_reason="manual",
            )
            if closed is None:
                return {"success": False, "error": "close_position returned None"}

            # Record the sell trade
            trade = Trade(
                position_id=position_id,
                market_id=position.market_id,
                direction=position.direction,
                action="sell",
                price=exit_price,
                amount_usdc=closed.size_usdc,
                paper_trading=True,
            )
            self.session.add(trade)
            self.session.commit()

            logger.info(
                "Paper position closed: id=%d pnl=%.4f exit_price=%.4f",
                position_id, closed.pnl_realized, exit_price,
            )
            return {
                "success": True,
                "pnl": closed.pnl_realized,
                "exit_price": exit_price,
                "paper": True,
            }

        except Exception as exc:
            logger.error("_sell_paper_position failed: %s", exc)
            try:
                self.session.rollback()
            except Exception:
                pass
            return {"success": False, "error": str(exc)}

    def _sell_real_position(self, position_id: int, amount_usdc: float = None) -> dict:
        """Skeleton for live CLOB sell order."""
        logger.warning(
            "Real trading: sell_position called for position_id=%d. "
            "Full py-clob-client integration required — order NOT sent.",
            position_id,
        )
        # TODO: implement with py-clob-client
        return {
            "success": False,
            "error": "Real trading not fully configured. Set PAPER_TRADING=true or complete CLOB integration.",
        }

    # ------------------------------------------------------------------
    # Portfolio helpers
    # ------------------------------------------------------------------

    def get_positions(self) -> list:
        """
        Return all currently open positions.

        Paper mode: queries the local DB.
        Real mode: would query the CLOB API (skeleton).
        """
        if self.paper_trading:
            return get_open_positions(self.session)
        else:
            logger.warning(
                "get_positions: real mode — CLOB integration not complete. Returning DB positions."
            )
            return get_open_positions(self.session)

    def get_balance(self) -> float:
        """
        Return the current available capital in USDC.

        Paper mode: derived from initial capital + realised PnL.
        Real mode: would query on-chain wallet balance (skeleton).
        """
        if self.paper_trading:
            return get_capital(self.session)
        else:
            logger.warning(
                "get_balance: real mode — wallet query not implemented. Returning DB capital."
            )
            # TODO: use web3 or Polymarket SDK to fetch USDC balance from wallet
            return get_capital(self.session)
