"""Generic fallback model: uses market price + Polymarket API history."""

import time
import logging
import requests
import numpy as np
from core.math_models.base_model import MathModel

logger = logging.getLogger(__name__)

GAMMA_API = 'https://gamma-api.polymarket.com'
HEADERS = {'User-Agent': 'PolymarketBot/2.0 Research'}


class GenericModel(MathModel):
    def _fetch_api_history(self, market_id: str, days: int = 7) -> list:
        """Fetch price history from Polymarket Gamma API (fallback when DB empty)."""
        endpoints = [
            f"{GAMMA_API}/markets/{market_id}/prices-history",
            f"{GAMMA_API}/prices-history?market={market_id}",
            f"{GAMMA_API}/markets/{market_id}/history",
        ]
        for url in endpoints:
            try:
                resp = requests.get(url, params={'days': days}, headers=HEADERS, timeout=8)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                records = data if isinstance(data, list) else data.get('history', data.get('prices', []))
                history = []
                for record in records:
                    ts = record.get('t', record.get('timestamp', record.get('time')))
                    price = record.get('p', record.get('price', record.get('yes_price')))
                    if ts is not None and price is not None:
                        try:
                            history.append({'timestamp': float(ts), 'yes_price': float(price)})
                        except (ValueError, TypeError):
                            continue
                if history:
                    logger.debug(f"GenericModel: fetched {len(history)} history points for {market_id} via API")
                    return history
            except Exception as e:
                logger.debug(f"GenericModel: API history endpoint failed ({url}): {e}")
        return []

    def calculate_probability(self, market, external_data=None) -> dict:
        yes_price = 0.5
        market_id = ''
        if hasattr(market, 'yes_price'):
            yes_price = market.yes_price
            market_id = getattr(market, 'market_id', '')
        elif isinstance(market, dict):
            yes_price = market.get('yes_price', 0.5)
            market_id = str(market.get('id') or market.get('market_id', ''))

        # Step 1: Try DB history
        history = []
        if market_id:
            try:
                from core.database import get_price_history
                if external_data and 'session' in external_data:
                    history = get_price_history(external_data['session'], market_id, days=7)
            except Exception:
                pass

        # Step 2: If DB empty (<5 points), fetch from Polymarket API
        if len(history) < 5 and market_id:
            history = self._fetch_api_history(market_id, days=7)

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
                    'confidence': 0.45,  # enough for edge_calculator tertiary condition (edge>=5%)
                    'method': 'generic_momentum',
                    'factors': {'momentum': round(momentum, 4), 'points': len(history), 'source': 'api_or_db'},
                    'reasoning': f'Generic momentum={momentum:+.1%}. Prob={prob:.1%}.'
                }

        # Pure market price — no history available.
        # confidence=0.30 + quaternary edge_calculator condition (edge>=10% AND conf>=0.25)
        return {
            'probability': yes_price,
            'confidence': 0.30,
            'method': 'generic_market_price',
            'factors': {'yes_price': yes_price},
            'reasoning': f'No model/history. Market price {yes_price:.1%} — requires strong edge (>=10%).'
        }
