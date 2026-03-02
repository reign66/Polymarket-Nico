import os
import time
import logging
import requests
from datetime import datetime
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

GAMMA_API = 'https://gamma-api.polymarket.com'
CLOB_API = 'https://clob.polymarket.com'
HEADERS = {'User-Agent': 'PolymarketBot/2.0 Research'}


class PolymarketClient:
    def __init__(self, db_session):
        self.session = db_session
        self.paper_trading = os.environ.get('PAPER_TRADING', 'true').lower() == 'true'

    def _request(self, url: str, params: dict = None, timeout: int = 10) -> Optional[dict | list]:
        """GET with retry x3, exponential backoff. Never crashes."""
        backoff = [1, 2, 4]
        for attempt, wait in enumerate(backoff, start=1):
            try:
                resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.HTTPError as e:
                logger.warning(f"HTTP error on attempt {attempt} for {url}: {e}")
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"Connection error on attempt {attempt} for {url}: {e}")
            except requests.exceptions.Timeout as e:
                logger.warning(f"Timeout on attempt {attempt} for {url}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error on attempt {attempt} for {url}: {e}")

            if attempt < len(backoff):
                time.sleep(wait)

        logger.error(f"All {len(backoff)} attempts failed for {url}")
        return None

    def get_markets(self, active: bool = True, closed: bool = False,
                    limit: int = 100, offset: int = 0) -> list:
        """Fetch list of markets from Gamma API."""
        url = f"{GAMMA_API}/markets"
        params = {
            'active': str(active).lower(),
            'closed': str(closed).lower(),
            'limit': limit,
            'offset': offset,
        }
        result = self._request(url, params=params)
        if result is None:
            return []
        if isinstance(result, list):
            return result
        # Some responses wrap in a dict
        return result.get('markets', result.get('data', []))

    def get_market(self, market_id: str) -> Optional[dict]:
        """Fetch a single market by ID from Gamma API."""
        url = f"{GAMMA_API}/markets/{market_id}"
        result = self._request(url)
        if result is None:
            return None
        if isinstance(result, dict):
            return result
        return None

    def get_orderbook(self, token_id: str) -> Optional[dict]:
        """Fetch orderbook from CLOB API and return structured data."""
        url = f"{CLOB_API}/book"
        params = {'token_id': token_id}
        data = self._request(url, params=params)
        if not data:
            return None

        try:
            raw_bids = data.get('bids', [])
            raw_asks = data.get('asks', [])

            bids = [{'price': float(b['price']), 'size': float(b['size'])} for b in raw_bids]
            asks = [{'price': float(a['price']), 'size': float(a['size'])} for a in raw_asks]

            best_bid = max((b['price'] for b in bids), default=None)
            best_ask = min((a['price'] for a in asks), default=None)

            spread = round(best_ask - best_bid, 4) if (best_bid is not None and best_ask is not None) else None
            midpoint = round((best_bid + best_ask) / 2, 4) if (best_bid is not None and best_ask is not None) else None

            return {
                'bids': bids,
                'asks': asks,
                'spread': spread,
                'midpoint': midpoint,
                'best_bid': best_bid,
                'best_ask': best_ask,
            }
        except Exception as e:
            logger.error(f"Error parsing orderbook for token {token_id}: {e}")
            return None

    def get_price_history(self, market_id: str, days: int = 14) -> list:
        """Fetch price history from Gamma API. Returns list of {timestamp, yes_price}."""
        endpoints = [
            f"{GAMMA_API}/markets/{market_id}/prices-history",
            f"{GAMMA_API}/prices-history?market={market_id}",
            f"{GAMMA_API}/markets/{market_id}/history",
        ]

        for url in endpoints:
            try:
                data = self._request(url, params={'days': days})
                if not data:
                    continue

                history = []
                records = data if isinstance(data, list) else data.get('history', data.get('prices', []))

                for record in records:
                    try:
                        ts = record.get('t', record.get('timestamp', record.get('time', None)))
                        price = record.get('p', record.get('price', record.get('yes_price', None)))
                        if ts is not None and price is not None:
                            history.append({
                                'timestamp': ts,
                                'yes_price': float(price),
                            })
                    except Exception:
                        continue

                if history:
                    logger.debug(f"Got {len(history)} price history records for market {market_id}")
                    return history

            except Exception as e:
                logger.warning(f"Price history endpoint {url} failed: {e}")
                continue

        logger.warning(f"No price history available for market {market_id}")
        return []

    def place_paper_bet(self, market_id: str, question: str, direction: str,
                        amount: float, price: float, niche: str,
                        math_edge: float, confidence: str = None) -> dict:
        """Create a paper trade position in the database."""
        from core.database import open_position, get_capital

        try:
            position = open_position(
                session=self.session,
                market_id=market_id,
                market_question=question,
                direction=direction,
                amount_usdc=amount,
                entry_price=price,
                bot_niche=niche,
                math_edge=math_edge,
                confidence=confidence,
            )

            capital = get_capital(self.session)
            position_id = position.id if position else None
            logger.info(
                f"[PAPER BET] market={market_id[:8]}, direction={direction}, "
                f"amount=${amount:.2f}, price={price:.3f}, edge={math_edge:.3f}, "
                f"confidence={confidence}, capital_remaining=${capital:.2f}, "
                f"position_id={position_id}"
            )

            return {'success': True, 'position_id': position_id}

        except Exception as e:
            logger.error(f"Failed to place paper bet for market {market_id}: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    def sell_paper_position(self, position_id: int, current_price: float) -> dict:
        """Close a paper trade position in the database."""
        from core.database import close_position

        try:
            closed = close_position(
                session=self.session,
                position_id=position_id,
                exit_price=current_price,
                exit_reason='manual-sell',
            )

            pnl = closed.pnl_realized if closed else 0.0
            logger.info(
                f"[PAPER SELL] position_id={position_id}, "
                f"exit_price={current_price:.3f}, pnl=${pnl:.2f}"
            )

            return {'success': True, 'pnl': pnl}

        except Exception as e:
            logger.error(f"Failed to sell paper position {position_id}: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}
