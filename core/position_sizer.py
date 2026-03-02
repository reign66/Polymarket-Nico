"""
core/position_sizer.py

Kelly-criterion-based position sizing with fractional scaling and hard caps.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class PositionSizer:
    """
    Compute optimal bet sizes using the fractional Kelly criterion.

    Parameters
    ----------
    config : dict
        Bot configuration dict.  The key ``kelly_fraction`` (float, default
        0.25) controls how aggressive the sizing is relative to the full Kelly
        recommendation.
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self.kelly_fraction: float = float(config.get("kelly_fraction", 0.25))
        logger.debug("PositionSizer initialised with kelly_fraction=%.2f", self.kelly_fraction)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def kelly_size(
        self,
        p_win: float,
        market_price: float,
        direction: str,
        bankroll: float,
        fraction: float = None,
    ) -> float:
        """
        Calculate the recommended bet size using the fractional Kelly criterion.

        Parameters
        ----------
        p_win : float
            Estimated probability of winning (0–1).
        market_price : float
            Current market price of the YES token (0–1).
        direction : str
            ``"YES"`` or ``"NO"``.
        bankroll : float
            Current available capital in USDC.
        fraction : float, optional
            Override the instance-level kelly_fraction for this call.

        Returns
        -------
        float
            Recommended bet size in USDC, rounded to 2 decimal places.
            Returns 0.0 whenever the Kelly criterion is negative or zero.
        """
        fraction = fraction if fraction is not None else self.kelly_fraction
        max_bet = float(os.environ.get("MAX_BET_SIZE", 50))

        if bankroll <= 0:
            logger.debug("kelly_size: bankroll=%.2f <= 0, returning 0.", bankroll)
            return 0.0

        if not (0.0 < p_win < 1.0):
            logger.debug("kelly_size: p_win=%.4f out of range (0, 1), returning 0.", p_win)
            return 0.0

        direction = direction.upper()

        # --- Odds calculation ---
        # ``odds`` is the net gain per unit risked on a winning bet.
        if direction == "YES":
            if market_price <= 0.0 or market_price >= 1.0:
                logger.debug(
                    "kelly_size: YES market_price=%.4f invalid, returning 0.", market_price
                )
                return 0.0
            # Buying YES at market_price: win (1 - market_price), risk market_price
            odds = (1.0 - market_price) / market_price

        elif direction == "NO":
            no_price = 1.0 - market_price
            if no_price <= 0.0 or no_price >= 1.0:
                logger.debug(
                    "kelly_size: NO no_price=%.4f invalid, returning 0.", no_price
                )
                return 0.0
            # Buying NO at no_price: win (1 - no_price) = market_price, risk no_price
            # Simplifies to: odds = market_price / (1 - market_price)
            odds = market_price / no_price

        else:
            logger.warning("kelly_size: unknown direction '%s', returning 0.", direction)
            return 0.0

        if odds <= 0:
            return 0.0

        # --- Full Kelly formula ---
        # f* = (odds * p - q) / odds   where q = 1 - p
        kelly = (odds * p_win - (1.0 - p_win)) / odds

        if kelly <= 0:
            logger.debug(
                "kelly_size: negative Kelly (%.4f) for p_win=%.4f odds=%.4f — no bet.",
                kelly, p_win, odds,
            )
            return 0.0

        # --- Apply fraction and caps ---
        size = kelly * fraction * bankroll

        # Max 5 % of bankroll per bet
        size = min(size, bankroll * 0.05)

        # Absolute cap from environment
        size = min(size, max_bet)

        # Never negative
        size = max(size, 0.0)

        result = round(size, 2)
        logger.debug(
            "kelly_size: dir=%s p_win=%.4f odds=%.4f kelly=%.4f fraction=%.2f "
            "bankroll=%.2f → size=%.2f",
            direction, p_win, odds, kelly, fraction, bankroll, result,
        )
        return result
