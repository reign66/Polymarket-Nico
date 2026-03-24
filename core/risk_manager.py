import os
import logging
from typing import Tuple

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, config: dict, db_session):
        self.config = config
        self.session = db_session
        self.risk_config = config.get('risk', {})
        self.api_limits = config.get('api_limits', {})

    def check_all(self, proposed_amount: float, market_id: str,
                  signal_info: dict = None) -> Tuple[bool, float, str]:
        """Run all risk checks. Returns (approved, adjusted_amount, reason).
        If approved=False, amount=0.
        If approved=True but amount adjusted, reason explains why."""

        from core.database import (get_daily_pnl, get_weekly_drawdown_pct,
                                   get_open_positions, get_positions_by_market,
                                   get_daily_exposure, get_monthly_api_cost, get_capital)

        try:
            capital = get_capital(self.session)
            amount = proposed_amount

            # a) Circuit breakers: daily loss
            daily_pnl = get_daily_pnl(self.session)
            daily_limit = capital * self.risk_config.get('daily_loss_limit_pct', 0.10)
            if daily_pnl < 0 and abs(daily_pnl) >= daily_limit:
                return (False, 0, f"Daily loss limit: {daily_pnl:.2f} >= {daily_limit:.2f}")

            # b) Weekly drawdown
            # NOTE: get_weekly_drawdown_pct returns a 0-100 percentage value.
            # weekly_drawdown_limit_pct in config is a fraction (e.g. 0.25 = 25%).
            weekly_dd = get_weekly_drawdown_pct(self.session, capital)  # e.g. 12.5 means 12.5%
            weekly_limit = self.risk_config.get('weekly_drawdown_limit_pct', 0.25) * 100  # convert to pct
            if weekly_dd >= weekly_limit:
                return (False, 0, f"Weekly drawdown: {weekly_dd:.1f}% >= {weekly_limit:.0f}%")

            # c) Max open positions
            open_pos = get_open_positions(self.session)
            max_pos = self.risk_config.get('max_open_positions', 5)
            if len(open_pos) >= max_pos:
                return (False, 0, f"Max positions: {len(open_pos)} >= {max_pos}")

            # d) Market dedup
            market_pos = get_positions_by_market(self.session, market_id)
            if market_pos:
                return (False, 0, f"Already in position on market {market_id[:8]}")

            # e) Daily capital exposure
            daily_exp = get_daily_exposure(self.session)
            small_cap = self.risk_config.get('small_cap_threshold', 2000)
            if capital < small_cap:
                daily_cap_limit = capital * self.risk_config.get('small_cap_daily_pct', 0.30)
            else:
                daily_cap_limit = capital * self.risk_config.get('standard_daily_pct', 0.40)

            # f) Exception: high edge + volume + confidence
            if signal_info:
                edge = signal_info.get('edge', 0)
                volume = signal_info.get('volume', 0)
                confidence = signal_info.get('confidence', 'LOW')
                exc_min_edge = self.risk_config.get('exception_min_edge', 0.25)
                exc_min_vol = self.risk_config.get('exception_min_volume', 50000)
                if edge > exc_min_edge and volume > exc_min_vol and confidence == 'HIGH':
                    bonus = self.risk_config.get('exception_bonus_pct', 0.20)
                    daily_cap_limit *= (1 + bonus)

            # g) Drawdown adjustments (weekly_dd is in 0-100 percentage units)
            if weekly_dd > 15:
                daily_cap_limit /= 2
            elif weekly_dd > 10:
                daily_cap_limit *= 0.75

            if daily_exp + amount > daily_cap_limit:
                # Reduce amount to fit
                amount = max(0, daily_cap_limit - daily_exp)
                if amount < 1.0:
                    return (False, 0, f"Daily exposure limit reached: {daily_exp:.2f}/{daily_cap_limit:.2f}")

            # h) Monthly API cost
            api_cost = get_monthly_api_cost(self.session)
            api_cost_eur = api_cost * 0.92
            max_api = self.api_limits.get('max_monthly_cost_eur', 5)
            if api_cost_eur >= max_api:
                return (False, 0, f"Monthly API cost: {api_cost_eur:.2f}€ >= {max_api}€")

            reason = ""
            if amount < proposed_amount:
                reason = f"Amount reduced from {proposed_amount:.2f} to {amount:.2f} (exposure limit)"

            logger.info(f"Risk check PASSED: amount={amount:.2f}, capital={capital:.2f}, "
                        f"daily_exp={daily_exp:.2f}, open_pos={len(open_pos)}")

            return (True, round(amount, 2), reason)

        except Exception as e:
            logger.error(f"Risk check error: {e}", exc_info=True)
            return (False, 0, f"Risk check error: {e}")
