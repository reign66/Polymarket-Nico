import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class ExitManager:
    def __init__(self, config: dict, db_session, pm_client, telegram=None):
        self.config = config
        self.session = db_session
        self.client = pm_client
        self.telegram = telegram
        self.exit_config = config.get('exit', {})

    def check_positions(self):
        """Check all open positions for exit conditions. Called every 30 min."""
        from core.database import get_open_positions, close_position

        try:
            positions = get_open_positions(self.session)
            if not positions:
                return

            logger.info(f"Checking {len(positions)} open positions")

            for pos in positions:
                try:
                    self._evaluate_position(pos)
                except Exception as e:
                    logger.error(f"Error evaluating position {pos.id}: {e}")

            self.session.commit()

        except Exception as e:
            logger.error(f"Exit manager error: {e}", exc_info=True)

    def _evaluate_position(self, position):
        """Evaluate a single position for exit conditions."""
        from core.database import close_position

        # Get current price
        market = self.client.get_market(position.market_id)
        if not market:
            logger.warning(f"Can't fetch market {position.market_id} for position {position.id}")
            return

        # Get current price for our direction
        if position.direction == 'YES':
            current_price = float(
                market.get('outcomePrices', '["0.5","0.5"]')
                .strip('[]').split(',')[0].strip('"')
                if isinstance(market.get('outcomePrices'), str)
                else market.get('yes_price', 0.5)
            )
        else:
            prices = market.get('outcomePrices', '["0.5","0.5"]')
            if isinstance(prices, str):
                try:
                    import json
                    parsed = json.loads(prices)
                    current_price = float(parsed[1]) if len(parsed) > 1 else 0.5
                except Exception:
                    current_price = 0.5
            else:
                current_price = float(market.get('no_price', 0.5))

        # Update current price and unrealized PnL
        position.current_price = current_price
        pnl_pct = (
            (current_price - position.entry_price) / position.entry_price
            if position.entry_price > 0
            else 0
        )
        position.pnl_unrealized = round(pnl_pct * position.amount_usdc, 2)

        take_profit = self.exit_config.get('take_profit_pct', 0.20)
        stop_loss_default = self.exit_config.get('stop_loss_pct', 0.15)
        near_hours = self.exit_config.get('near_resolution_hours', 48)

        # ── ADAPTIVE STOP LOSS (V2.3 fix) ──────────────────────────────
        # Low-price markets (< 8¢) are hyper-volatile: a 2¢ move = -50%.
        # We can't catch -15% in 30 min intervals on these markets.
        # Use tighter stop loss for low-price entries.
        entry = position.entry_price or 0.5
        if entry < 0.05:
            # Very low price (< 5¢): stop at -20% to catch faster
            stop_loss = 0.10
        elif entry < 0.08:
            # Low price (5-8¢): tighter stop
            stop_loss = 0.12
        elif entry < 0.15:
            stop_loss = 0.13
        else:
            stop_loss = stop_loss_default

        # Take profit
        if pnl_pct >= take_profit:
            logger.info(f"TAKE PROFIT: position {position.id}, pnl={pnl_pct:.1%}")
            self._execute_exit(position, current_price, 'take-profit')
            return

        # Stop loss (adaptive)
        if pnl_pct <= -stop_loss:
            logger.info(f"STOP LOSS: position {position.id}, pnl={pnl_pct:.1%} (threshold={stop_loss:.0%}, entry={entry:.3f})")
            self._execute_exit(position, current_price, 'stop-loss')
            return

        # Near resolution alert (don't auto-sell, just alert)
        end_date_str = market.get('endDate', market.get('end_date_iso', ''))
        if end_date_str and pnl_pct > 0:
            try:
                end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                hours_left = (end_date - datetime.now(end_date.tzinfo)).total_seconds() / 3600
                if 0 < hours_left < near_hours:
                    if self.telegram:
                        self.telegram.send_near_resolution_alert(position, hours_left)
            except Exception:
                pass

    def _execute_exit(self, position, exit_price: float, reason: str):
        """Execute position exit (paper mode)."""
        from core.database import close_position

        try:
            # Close in DB
            closed = close_position(self.session, position.id, exit_price, reason)

            # Telegram notification
            if self.telegram and closed:
                self.telegram.send_exit_notification(closed)

            logger.info(
                f"Position {position.id} closed: reason={reason}, "
                f"entry={position.entry_price}, exit={exit_price}, "
                f"pnl={closed.pnl_realized if closed else 'N/A'}"
            )
        except Exception as e:
            logger.error(f"Exit execution error for position {position.id}: {e}")

    def force_close(self, position_id: int, reason: str = 'manual'):
        """Force close a position."""
        from core.database import get_open_positions

        positions = get_open_positions(self.session)
        pos = next((p for p in positions if p.id == position_id), None)
        if not pos:
            logger.warning(f"Position {position_id} not found or already closed")
            return None

        market = self.client.get_market(pos.market_id)
        current_price = pos.current_price or pos.entry_price
        if market:
            # Parse current price from market
            try:
                import json
                prices = json.loads(market.get('outcomePrices', '["0.5","0.5"]'))
                idx = 0 if pos.direction == 'YES' else 1
                current_price = float(prices[idx])
            except Exception:
                pass

        self._execute_exit(pos, current_price, reason)
