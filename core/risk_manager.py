"""
core/risk_manager.py

Portfolio-level risk controls:
- Circuit breakers (daily loss, weekly drawdown, open position count, API cost)
- Daily capital exposure limits with dynamic drawdown adjustments
- Market deduplication guard
- Liquidity filters
"""

import logging
import os
from datetime import datetime
from typing import Tuple

from core.database import (
    Session,
    get_capital,
    get_daily_exposure,
    get_daily_pnl,
    get_monthly_api_cost,
    get_open_positions,
    get_weekly_drawdown_pct,
)

logger = logging.getLogger(__name__)

# USD → EUR conversion used for API cost comparisons
_USD_TO_EUR = 0.92


class RiskManager:
    """
    Centralised risk gating for the Polymarket bot.

    All ``check_*`` methods return a ``(ok: bool, reason: str)`` tuple.
    When ``ok`` is True the reason string is always empty.

    Parameters
    ----------
    config : dict
        Full bot configuration dict.  Expected keys (with nested dicts):
        - risk_controls.daily_loss_pct
        - risk_controls.weekly_drawdown_pct
        - risk_controls.max_open_positions
        - risk_controls.max_monthly_api_cost_eur
        - capital_limits.small_cap_threshold
        - capital_limits.small_cap_daily_pct
        - capital_limits.standard_daily_pct
        - capital_limits.exception_min_edge
        - capital_limits.exception_min_volume
        - capital_limits.exception_bonus_pct
        - min_market_volume
        - max_spread
    db_session : sqlalchemy.orm.Session
    """

    def __init__(self, config: dict, db_session: Session) -> None:
        self.config = config
        self.session = db_session

    # ------------------------------------------------------------------
    # Circuit breakers
    # ------------------------------------------------------------------

    def check_circuit_breakers(self) -> Tuple[bool, str]:
        """
        Run all portfolio-level circuit breakers in sequence.

        Returns ``(True, "")`` only when every check passes.
        Returns ``(False, <reason>)`` on the first failing check.
        """
        risk = self.config.get("risk_controls", {})

        capital = get_capital(self.session)

        # 1. Daily loss limit
        daily_pnl = get_daily_pnl(self.session)
        daily_loss_pct = float(risk.get("daily_loss_pct", 0.05))
        daily_loss_limit = capital * daily_loss_pct

        if daily_pnl < 0 and abs(daily_pnl) >= daily_loss_limit:
            reason = (
                f"Daily loss limit reached: {daily_pnl:.2f} EUR "
                f"(limit: -{daily_loss_limit:.2f} EUR)"
            )
            logger.warning("Circuit breaker triggered — %s", reason)
            return (False, reason)

        # 2. Weekly drawdown
        weekly_dd_pct = get_weekly_drawdown_pct(self.session, capital)
        max_weekly_dd = float(risk.get("weekly_drawdown_pct", 0.20)) * 100.0  # stored as decimal

        if weekly_dd_pct >= max_weekly_dd:
            reason = (
                f"Weekly drawdown limit reached: {weekly_dd_pct:.2f}% "
                f"(limit: {max_weekly_dd:.2f}%)"
            )
            logger.warning("Circuit breaker triggered — %s", reason)
            return (False, reason)

        # 3. Max open positions
        open_positions = get_open_positions(self.session)
        max_open = int(risk.get("max_open_positions", 10))

        if len(open_positions) >= max_open:
            reason = (
                f"Max open positions reached: {len(open_positions)} "
                f"(limit: {max_open})"
            )
            logger.warning("Circuit breaker triggered — %s", reason)
            return (False, reason)

        # 4. Monthly API cost
        monthly_cost_usd = get_monthly_api_cost(self.session)
        monthly_cost_eur = monthly_cost_usd * _USD_TO_EUR
        max_monthly_cost_eur = float(risk.get("max_monthly_api_cost_eur", 50.0))

        if monthly_cost_eur >= max_monthly_cost_eur:
            reason = (
                f"Monthly API cost limit reached: {monthly_cost_eur:.2f} EUR "
                f"(limit: {max_monthly_cost_eur:.2f} EUR)"
            )
            logger.warning("Circuit breaker triggered — %s", reason)
            return (False, reason)

        return (True, "")

    # ------------------------------------------------------------------
    # Daily capital exposure
    # ------------------------------------------------------------------

    def check_daily_capital_exposure(
        self, proposed_amount: float, signal: dict
    ) -> Tuple[bool, str]:
        """
        Verify that adding ``proposed_amount`` would not exceed the daily
        exposure limit, including any high-conviction exceptions or
        drawdown-based reductions.

        Parameters
        ----------
        proposed_amount : float
            USDC amount of the bet being considered.
        signal : dict
            Signal dict with keys: edge (float), volume (float),
            confidence (str: LOW/MEDIUM/HIGH).

        Returns
        -------
        (bool, str)
        """
        limits = self.config.get("capital_limits", {})

        capital = get_capital(self.session)
        daily_exposure = get_daily_exposure(self.session)

        small_cap_threshold = float(limits.get("small_cap_threshold", 500.0))
        small_cap_daily_pct = float(limits.get("small_cap_daily_pct", 0.10))
        standard_daily_pct = float(limits.get("standard_daily_pct", 0.05))

        # Base daily limit
        if capital < small_cap_threshold:
            daily_limit = capital * small_cap_daily_pct
        else:
            daily_limit = capital * standard_daily_pct

        # High-conviction exception: ALL three conditions must be met
        edge = float(signal.get("edge", 0.0))
        volume = float(signal.get("volume", 0.0))
        confidence = str(signal.get("confidence", "LOW")).upper()

        exception_min_edge = float(limits.get("exception_min_edge", 0.10))
        exception_min_volume = float(limits.get("exception_min_volume", 10000.0))
        exception_bonus_pct = float(limits.get("exception_bonus_pct", 0.50))

        if (
            edge > exception_min_edge
            and volume > exception_min_volume
            and confidence == "HIGH"
        ):
            daily_limit *= 1.0 + exception_bonus_pct
            logger.debug(
                "check_daily_capital_exposure: high-conviction exception applied, "
                "new daily_limit=%.2f", daily_limit
            )

        # Drawdown-based reductions
        weekly_dd = get_weekly_drawdown_pct(self.session, capital)
        if weekly_dd > 15.0:
            daily_limit /= 2.0
            logger.debug(
                "check_daily_capital_exposure: weekly_dd=%.2f%% > 15%%, daily_limit halved to %.2f",
                weekly_dd, daily_limit,
            )
        elif weekly_dd > 10.0:
            daily_limit *= 0.75
            logger.debug(
                "check_daily_capital_exposure: weekly_dd=%.2f%% > 10%%, daily_limit reduced to %.2f",
                weekly_dd, daily_limit,
            )

        projected_exposure = daily_exposure + proposed_amount
        if projected_exposure > daily_limit:
            reason = (
                f"Daily exposure limit: {projected_exposure:.2f} > {daily_limit:.2f} "
                f"(current exposure: {daily_exposure:.2f}, proposed: {proposed_amount:.2f})"
            )
            logger.warning("check_daily_capital_exposure failed — %s", reason)
            return (False, reason)

        return (True, "")

    # ------------------------------------------------------------------
    # Market deduplication
    # ------------------------------------------------------------------

    def check_market_dedup(self, market_id: str) -> Tuple[bool, str]:
        """
        Reject a bet if the bot already holds an open position on the same market.

        Returns
        -------
        (bool, str)
        """
        try:
            from core.database import Position

            existing = (
                self.session.query(Position)
                .filter(
                    Position.market_id == market_id,
                    Position.status == "open",
                )
                .first()
            )
            if existing is not None:
                reason = (
                    f"Market already has open position (position_id={existing.id}, "
                    f"market_id={market_id})"
                )
                logger.debug("check_market_dedup: %s", reason)
                return (False, reason)
            return (True, "")
        except Exception as exc:
            logger.error("check_market_dedup failed: %s", exc)
            # Fail safe: block the bet when the check itself errors
            return (False, f"Dedup check error: {exc}")

    # ------------------------------------------------------------------
    # Liquidity filter
    # ------------------------------------------------------------------

    def check_liquidity(self, volume: float, spread: float) -> Tuple[bool, str]:
        """
        Ensure the market meets minimum volume and maximum spread requirements.

        Parameters
        ----------
        volume : float
            24-hour or total trading volume in USDC.
        spread : float
            Absolute spread: ``|1 - yes_price - no_price|``.

        Returns
        -------
        (bool, str)
        """
        min_volume = float(self.config.get("min_market_volume", 1000.0))
        max_spread = float(self.config.get("max_spread", 0.05))

        if volume < min_volume:
            reason = f"Volume too low: {volume:.2f} (minimum: {min_volume:.2f})"
            logger.debug("check_liquidity: %s", reason)
            return (False, reason)

        if spread > max_spread:
            reason = f"Spread too wide: {spread:.4f} (maximum: {max_spread:.4f})"
            logger.debug("check_liquidity: %s", reason)
            return (False, reason)

        return (True, "")
