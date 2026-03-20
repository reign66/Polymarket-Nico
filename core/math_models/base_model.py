"""Base class for all math probability models."""

import logging

logger = logging.getLogger(__name__)


class MathModel:
    def calculate_probability(self, market, external_data=None) -> dict:
        """
        Calculate probability for a market.

        Returns dict with:
            probability  - float [0,1]
            confidence   - float [0,1]
            method       - str describing the model/method used
            factors      - dict of intermediate values
            reasoning    - str human-readable explanation
        """
        raise NotImplementedError

    def _fallback(self, market) -> dict:
        """Fallback when model cannot calculate.

        confidence=0.20 so quaternary edge_calculator condition (edge>=10% AND conf>=0.20)
        can still trigger AI on very high-edge markets.
        """
        yes_price = 0.5
        if hasattr(market, 'yes_price'):
            yes_price = market.yes_price
        elif isinstance(market, dict):
            yes_price = market.get('yes_price', 0.5)
        return {
            'probability': yes_price,
            'confidence': 0.20,
            'method': 'fallback_market_price',
            'factors': {},
            'reasoning': 'Model could not process this market. Using market price as estimate.'
        }
