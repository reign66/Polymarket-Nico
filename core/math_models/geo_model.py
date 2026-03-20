"""Geopolitics model: price momentum + GDELT news catalyst."""

import time
import logging
import numpy as np
from core.math_models.base_model import MathModel

logger = logging.getLogger(__name__)


class GeoModel(MathModel):
    def calculate_probability(self, market, external_data=None) -> dict:
        market_id = ''
        yes_price = 0.5
        question = ''
        if hasattr(market, 'market_id'):
            market_id = market.market_id
            yes_price = market.yes_price
            question = getattr(market, 'question', '')
        elif isinstance(market, dict):
            market_id = str(market.get('id') or market.get('market_id', ''))
            yes_price = market.get('yes_price', 0.5)
            question = market.get('question', '')

        # GDELT news signal
        news_boost = 0.0
        news_info = {}
        try:
            from tools.news_fetcher import get_news_fetcher
            fetcher = get_news_fetcher()
            signal = fetcher.get_news_signal('geopolitics', question)
            news_boost = signal.get('boost', 0.0)
            news_info = {'articles': signal['articles'], 'tone': signal['tone']}
        except Exception as e:
            logger.debug(f"GDELT geo signal failed: {e}")

        # Try to get price history from DB
        history = []
        if market_id:
            try:
                from core.database import get_price_history
                if external_data and 'session' in external_data:
                    history = get_price_history(external_data['session'], market_id, days=14)
            except Exception as e:
                logger.debug(f"Could not fetch price history for {market_id}: {e}")

        if len(history) < 10:
            q_lower = question.lower()
            prior_offset = 0.0
            if any(kw in q_lower for kw in [
                "ceasefire", "peace deal", "peace agreement", "peace treaty",
                "resolve", "end the war", "withdrawal", "diplomacy"
            ]):
                prior_offset = -0.10
            elif any(kw in q_lower for kw in ["war", "invasion", "conflict", "attack"]):
                prior_offset = -0.05

            prob = float(np.clip(yes_price + prior_offset, 0.03, 0.97))
            # GDELT boost applied to confidence (not probability — let AI decide direction)
            base_conf = 0.35
            confidence = min(0.55, base_conf + (0.10 if news_info.get('articles', 0) >= 3 else 0.0))
            return {
                'probability': prob,
                'confidence': confidence,
                'method': 'geo_base_rate_prior+GDELT',
                'factors': {'data_points': len(history), 'prior_offset': prior_offset, **news_info},
                'reasoning': (
                    f'No history. Offset={prior_offset:+.0%}. '
                    f'GDELT {news_info.get("articles",0)} articles tone={news_info.get("tone",0):.1f}. '
                    f'Prob={prob:.1%} vs market {yes_price:.1%}'
                )
            }

        prices = [h['yes_price'] for h in history]
        timestamps = [h['timestamp'] for h in history]

        week_cutoff = time.time() - 7 * 86400
        week_prices = [p for t, p in zip(timestamps, prices) if t >= week_cutoff]
        if len(week_prices) >= 2:
            momentum = (week_prices[-1] - week_prices[0]) / max(week_prices[0], 0.01)
        else:
            momentum = 0

        vol = np.std(prices[-10:]) if len(prices) >= 10 else (np.std(prices) if len(prices) >= 3 else 0.05)

        prob = yes_price + (momentum * 0.25)
        prob = max(0.03, min(0.97, prob))

        confidence = 0.20
        if abs(momentum) > 0.10:
            confidence += 0.10
        if abs(momentum) > 0.20:
            confidence += 0.10
        if vol > 0.05:
            confidence += 0.05
        if news_info.get('articles', 0) >= 3:
            confidence += 0.08  # GDELT confirms active news cycle
        confidence = min(confidence, 0.55)

        return {
            'probability': prob,
            'confidence': confidence,
            'method': f'geo_momentum({momentum:+.1%}/7d)+GDELT',
            'factors': {
                'momentum_7d': round(momentum, 4),
                'vol': round(vol, 4),
                'points': len(prices),
                **news_info,
            },
            'reasoning': (
                f'Momentum={momentum:+.1%}, vol={vol:.3f}. '
                f'GDELT {news_info.get("articles",0)} articles tone={news_info.get("tone",0):.1f}. '
                f'Prob={prob:.1%} vs market {yes_price:.1%}'
            )
        }
