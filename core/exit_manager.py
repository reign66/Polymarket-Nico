"""
core/exit_manager.py

Automated position monitoring and exit logic.

Called on a periodic schedule (every 30 min recommended) via ``check_positions``.
Applies take-profit, stop-loss, and near-resolution alerts to all open positions.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from core.database import (
    Session,
    close_position,
    get_open_positions,
)

logger = logging.getLogger(__name__)

# Default exit thresholds (can be overridden by config)
_DEFAULT_TAKE_PROFIT_PCT = 0.20   # +20 %
_DEFAULT_STOP_LOSS_PCT = 0.15     # -15 %
_NEAR_RESOLUTION_HOURS = 48


class ExitManager:
    """
    Monitors open positions and executes automated exit rules.

    Parameters
    ----------
    config : dict
        Bot configuration dict.  Expected key: ``exit_rules`` dict with
        ``take_profit_pct`` (float) and ``stop_loss_pct`` (float).
    db_session : sqlalchemy.orm.Session
    polymarket_client : PolymarketClient
        Used to fetch live market prices and execute sells.
    telegram_alerter : optional
        Object with a ``send_exit_notification(position, pnl_info)`` method.
        Notifications are silently skipped when this is None.
    """

    def __init__(
        self,
        config: dict,
        db_session: Session,
        polymarket_client,
        telegram_alerter=None,
    ) -> None:
        self.config = config
        self.session = db_session
        self.client = polymarket_client
        self.telegram = telegram_alerter

        exit_rules = config.get("exit_rules", {})
        self.take_profit_pct: float = float(
            exit_rules.get("take_profit_pct", _DEFAULT_TAKE_PROFIT_PCT)
        )
        self.stop_loss_pct: float = float(
            exit_rules.get("stop_loss_pct", _DEFAULT_STOP_LOSS_PCT)
        )
        logger.info(
            "ExitManager initialised: take_profit=%.0f%% stop_loss=%.0f%%",
            self.take_profit_pct * 100,
            self.stop_loss_pct * 100,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _send_notification(self, position, pnl_info: dict) -> None:
        """Fire-and-forget Telegram notification; never raises."""
        if self.telegram is None:
            return
        try:
            self.telegram.send_exit_notification(position, pnl_info)
        except Exception as exc:
            logger.warning("Telegram notification failed: %s", exc)

    @staticmethod
    def _parse_end_date(end_date_str: str) -> Optional[datetime]:
        """Try several ISO-8601 variants; return None on failure."""
        if not end_date_str:
            return None
        for fmt in (
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(end_date_str[:len(fmt)], fmt)
            except ValueError:
                continue
        logger.debug("_parse_end_date: unrecognised format '%s'", end_date_str)
        return None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def check_positions(self) -> None:
        """
        Evaluate every open position and apply exit rules.

        Should be called every 30 minutes.  Commits all DB mutations at the
        end of a successful run; rolls back on unhandled error.
        """
        positions = get_open_positions(self.session)
        if not positions:
            logger.debug("check_positions: no open positions.")
            return

        logger.info("check_positions: evaluating %d open position(s).", len(positions))
        now = datetime.utcnow()

        for position in positions:
            try:
                self._evaluate_position(position, now)
            except Exception as exc:
                logger.error(
                    "check_positions: unhandled error for position_id=%d — %s",
                    position.id,
                    exc,
                )

        # Commit any latent-PnL updates not yet committed by individual exits
        try:
            self.session.commit()
        except Exception as exc:
            logger.error("check_positions: final commit failed — %s", exc)
            try:
                self.session.rollback()
            except Exception:
                pass

    def _evaluate_position(self, position, now: datetime) -> None:
        """Apply exit rules to a single open position."""
        # --- Fetch live market data ---
        details = self.client.get_market_details(position.market_id)
        if details is None:
            logger.warning(
                "_evaluate_position: could not fetch details for market=%s, skipping.",
                position.market_id,
            )
            return

        # Current price for the direction we hold
        current_price = (
            details["yes_price"] if position.direction == "YES"
            else details["no_price"]
        )

        # Update current_price in the position record (will be committed in bulk)
        position.current_price = current_price

        # --- P&L percentage ---
        if position.entry_price and position.entry_price != 0:
            pnl_pct = (current_price - position.entry_price) / position.entry_price
        else:
            pnl_pct = 0.0

        # Update latent PnL in USDC
        if position.entry_price and position.entry_price != 0:
            latent_pnl = (
                (current_price - position.entry_price)
                * (position.size_usdc / position.entry_price)
            )
        else:
            latent_pnl = 0.0
        position.pnl_latent = round(latent_pnl, 4)

        logger.debug(
            "_evaluate_position: id=%d market=%s dir=%s entry=%.4f current=%.4f pnl_pct=%.2f%%",
            position.id,
            position.market_id,
            position.direction,
            position.entry_price,
            current_price,
            pnl_pct * 100,
        )

        # --- Take-profit ---
        if pnl_pct >= self.take_profit_pct:
            logger.info(
                "Take-profit triggered: position_id=%d pnl_pct=%.2f%%",
                position.id, pnl_pct * 100,
            )
            self._execute_exit(position, current_price, exit_reason="take-profit", pnl_pct=pnl_pct)
            return

        # --- Stop-loss ---
        if pnl_pct <= -self.stop_loss_pct:
            logger.info(
                "Stop-loss triggered: position_id=%d pnl_pct=%.2f%%",
                position.id, pnl_pct * 100,
            )
            self._execute_exit(position, current_price, exit_reason="stop-loss", pnl_pct=pnl_pct)
            return

        # --- Near-resolution alert ---
        end_date = self._parse_end_date(details.get("end_date", ""))
        if end_date is not None:
            time_left = end_date - now
            if timedelta(0) < time_left < timedelta(hours=_NEAR_RESOLUTION_HOURS):
                if pnl_pct > 0:
                    hours_left = time_left.total_seconds() / 3600
                    logger.info(
                        "Near-resolution alert: position_id=%d market=%s "
                        "%.1fh until end, pnl_pct=%.2f%%",
                        position.id, position.market_id, hours_left, pnl_pct * 100,
                    )
                    pnl_info = {
                        "pnl_pct": pnl_pct,
                        "pnl_usdc": latent_pnl,
                        "current_price": current_price,
                        "entry_price": position.entry_price,
                        "hours_left": hours_left,
                        "exit_reason": "near-resolution",
                    }
                    self._send_notification(position, pnl_info)

    def _execute_exit(
        self, position, exit_price: float, exit_reason: str, pnl_pct: float
    ) -> None:
        """
        Sell a position and close it in the DB, then notify.

        Uses the client's ``sell_position`` to handle paper vs. real logic,
        then calls ``close_position`` explicitly to ensure the exit_reason and
        DB state are correct.
        """
        sell_result = self.client.sell_position(position.id)

        if not sell_result.get("success"):
            logger.error(
                "_execute_exit: sell_position failed for id=%d: %s",
                position.id,
                sell_result.get("error"),
            )
            # close_position directly as fallback
            close_position(
                self.session,
                position_id=position.id,
                exit_price=exit_price,
                exit_reason=exit_reason,
            )
        else:
            # sell_position already called close_position internally;
            # update the exit_reason if it was set to 'manual' by default
            from core.database import Position as PositionModel

            db_pos = (
                self.session.query(PositionModel)
                .filter(PositionModel.id == position.id)
                .first()
            )
            if db_pos and db_pos.exit_reason != exit_reason:
                db_pos.exit_reason = exit_reason
                self.session.commit()

        realized_pnl = sell_result.get("pnl", position.pnl_latent)
        pnl_info = {
            "pnl_pct": pnl_pct,
            "pnl_usdc": realized_pnl,
            "current_price": exit_price,
            "entry_price": position.entry_price,
            "exit_reason": exit_reason,
        }
        self._send_notification(position, pnl_info)

    # ------------------------------------------------------------------
    # Manual close
    # ------------------------------------------------------------------

    def force_close_position(self, position_id: int, reason: str = "manual") -> dict:
        """
        Immediately close a specific open position regardless of P&L.

        Parameters
        ----------
        position_id : int
            DB id of the position to close.
        reason : str
            Exit reason label stored in the DB (default: ``"manual"``).

        Returns
        -------
        dict
            ``{success, pnl_usdc, pnl_pct, exit_price, position_id}`` on success.
            ``{success: False, error: str}`` on failure.
        """
        try:
            from core.database import Position as PositionModel

            position = (
                self.session.query(PositionModel)
                .filter(
                    PositionModel.id == position_id,
                    PositionModel.status == "open",
                )
                .first()
            )
            if position is None:
                return {
                    "success": False,
                    "error": f"Open position id={position_id} not found",
                }

            # Attempt to fetch live price for the exit
            details = self.client.get_market_details(position.market_id)
            if details:
                exit_price = (
                    details["yes_price"] if position.direction == "YES"
                    else details["no_price"]
                )
            else:
                exit_price = position.current_price
                logger.warning(
                    "force_close_position: using last known price=%.4f for market=%s",
                    exit_price, position.market_id,
                )

            sell_result = self.client.sell_position(position_id)
            if not sell_result.get("success"):
                # Fallback: close directly in DB
                logger.warning(
                    "force_close_position: sell_position failed, closing via DB. error=%s",
                    sell_result.get("error"),
                )
                closed = close_position(
                    self.session,
                    position_id=position_id,
                    exit_price=exit_price,
                    exit_reason=reason,
                )
                if closed is None:
                    return {"success": False, "error": "close_position returned None"}
                realized_pnl = closed.pnl_realized or 0.0
            else:
                # Update exit_reason to the caller's label
                db_pos = (
                    self.session.query(PositionModel)
                    .filter(PositionModel.id == position_id)
                    .first()
                )
                if db_pos:
                    db_pos.exit_reason = reason
                    self.session.commit()
                realized_pnl = sell_result.get("pnl", 0.0)

            pnl_pct = (
                (exit_price - position.entry_price) / position.entry_price
                if position.entry_price and position.entry_price != 0
                else 0.0
            )

            pnl_info = {
                "pnl_pct": pnl_pct,
                "pnl_usdc": realized_pnl,
                "current_price": exit_price,
                "entry_price": position.entry_price,
                "exit_reason": reason,
            }
            self._send_notification(position, pnl_info)

            logger.info(
                "force_close_position: id=%d closed. pnl=%.4f reason=%s",
                position_id, realized_pnl, reason,
            )
            return {
                "success": True,
                "position_id": position_id,
                "pnl_usdc": realized_pnl,
                "pnl_pct": round(pnl_pct * 100, 2),
                "exit_price": exit_price,
            }

        except Exception as exc:
            logger.error("force_close_position failed: %s", exc)
            try:
                self.session.rollback()
            except Exception:
                pass
            return {"success": False, "error": str(exc)}
