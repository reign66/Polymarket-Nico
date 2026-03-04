"""Generic fallback model: uses market price with minimal confidence."""

import time
import logging
import numpy as np
from core.math_models.base_model import MathModel

logger = logging.getLogger(__name__)


class GenericModel(MathModel):
    def calculate_probability(self, market, external_data=None) -> dict:
        yes_price = 0.5
        market_id = ''
        if hasattr(market, 'yes_price'):
            yes_price = market.yes_price
            market_id = getattr(market, 'market_id', '')
        elif isinstance(market, dict):
            yes_price = market.get('yes_price', 0.5)
            market_id = str(market.get('id') or market.get('market_id', ''))

        # Try momentum if we have history
        history = []
        if market_id:
            try:
                from core.database import get_price_history
                if external_data and 'session' in external_data:
                    history = get_price_history(external_data['session'], market_id, days=7)
            except Exception:
                pass

        if len(history) >= 5:
            prices = [h['yes_price'] for h in history]
            timestamps = [h['timestamp'] for h in history]
            week_cutoff = time.time() - 7 * 86400
            week_prices = [p for t, p in zip(timestamps, prices) if t >= week_cutoff]
            if len(week_prices) >= 2:
                momentum = (week_prices[-1] - week_prices[0]) / max(week_prices[0], 0.01)
                prob = max(0.03, min(0.97, yes_price + momentum * 0.10))
                return {
                    'probability': prob,
                    'confidence': 0.08,
                    'method': 'generic_momentum',
                    'factors': {'momentum': round(momentum, 4), 'points': len(history)},
                    'reasoning': f'Generic momentum={momentum:+.1%}. Prob={prob:.1%}.'
                }

        # Pure market price - confidence so low it never triggers AI
        return {
            'probability': yes_price,
            'confidence': 0.05,
            'method': 'generic_market_price',
            'factors': {'yes_price': yes_price},
            'reasoning': f'No model available. Using market price {yes_price:.1%} with minimal confidence.'
        }
