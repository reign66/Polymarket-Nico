import os
import logging

logger = logging.getLogger(__name__)


class PositionSizer:
    def __init__(self, config: dict):
        self.config = config
        self.kelly_config = config.get('kelly', {})

    def calculate_size(self, edge_result, bankroll: float) -> float:
        """Calculate bet size using Kelly criterion from edge_result.

        edge_result already has kelly_fraction computed by EdgeCalculator.
        We just apply it to bankroll with safety caps.
        """
        kelly = edge_result.kelly_fraction

        if kelly <= 0 or bankroll <= 0:
            return 0.0

        bet_amount = bankroll * kelly

        # Minimum bet: $1
        bet_amount = max(1.0, bet_amount)

        # Cap at max_bet_pct of bankroll (default 5%)
        max_pct = self.kelly_config.get('max_bet_pct', 0.05)
        bet_amount = min(bet_amount, bankroll * max_pct)

        # Absolute cap from env
        max_bet = float(os.environ.get('MAX_BET_SIZE', 50))
        bet_amount = min(bet_amount, max_bet)

        # Don't bet more than we have
        bet_amount = min(bet_amount, bankroll * 0.95)  # Keep 5% reserve

        return round(bet_amount, 2)
