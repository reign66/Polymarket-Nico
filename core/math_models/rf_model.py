"""
core/math_models/rf_model.py — Random Forest probability model.

Architecture (from article):
  - 100 trees, each votes on YES/NO
  - Features: price, volume_24h, momentum_7d, days_to_expiry, liquidity
  - Entry only when: market_price <= model_prob * 0.5  (2x undervalued)
  - Min model confidence: 70%+

Training data: last 500 resolved markets from Gamma API.
Retrains every 6h automatically.
"""

import time
import math
import logging
import requests
import numpy as np
from datetime import datetime, timezone
from core.math_models.base_model import MathModel

logger = logging.getLogger(__name__)

GAMMA_API = 'https://gamma-api.polymarket.com'
HEADERS = {'User-Agent': 'PolymarketBot/2.0 Research'}

NICHE_ENCODE = {
    'crypto': 0, 'nba': 1, 'politics': 2, 'geopolitics': 3,
    'f1': 4, 'golf': 5, 'soccer': 6, 'mma': 7,
    'entertainment': 8, 'science': 9, 'tech': 10, 'generic': 11,
}


def _log1p(x):
    return math.log1p(max(0.0, float(x or 0)))


def _days_left(end_date_str) -> float:
    if not end_date_str:
        return 30.0
    try:
        if 'T' in str(end_date_str):
            dt = datetime.fromisoformat(str(end_date_str).replace('Z', '+00:00'))
        else:
            dt = datetime.strptime(str(end_date_str), '%Y-%m-%d').replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0.0, (dt - now).total_seconds() / 86400)
    except Exception:
        return 30.0


class RFModel(MathModel):
    """Random Forest probability model — replaces generic fallback."""

    RF_ENTRY_RATIO = 0.50   # buy when market_price <= model_prob * 0.50
    RF_MIN_CONF = 0.70       # model must be >= 70% confident

    def __init__(self):
        self._model = None
        self._trained_at = 0.0
        self._retrain_interval = 6 * 3600   # retrain every 6h
        self._n_training_samples = 0

    # ------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------

    def _extract_features(self, market, momentum_7d: float = 0.0) -> list:
        """
        Features (matches article):
          [yes_price, log_volume, momentum_7d, days_to_expiry, log_liquidity]
        """
        yes_price = market.yes_price if hasattr(market, 'yes_price') else market.get('yes_price', 0.5)
        volume = market.volume if hasattr(market, 'volume') else market.get('volume', 0)
        liquidity = market.liquidity if hasattr(market, 'liquidity') else market.get('liquidity', 0)
        end_date = market.end_date if hasattr(market, 'end_date') else market.get('end_date', '')

        return [
            float(yes_price),
            _log1p(volume),
            float(momentum_7d),
            _days_left(end_date),
            _log1p(liquidity),
        ]

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def _fetch_resolved_markets(self, limit: int = 500) -> list:
        """Fetch recently resolved markets from Gamma API."""
        try:
            resp = requests.get(
                f"{GAMMA_API}/markets",
                params={'active': 'false', 'closed': 'true', 'limit': limit},
                headers=HEADERS, timeout=20
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            items = data if isinstance(data, list) else data.get('markets', data.get('data', []))
            return items
        except Exception as e:
            logger.warning(f"RF: failed to fetch resolved markets: {e}")
            return []

    def _fetch_price_history(self, market_id: str) -> float:
        """
        Try to get a pre-resolution price for a market.
        Returns the price ~50% through market life (mid-life proxy).
        """
        endpoints = [
            f"{GAMMA_API}/markets/{market_id}/prices-history",
            f"{GAMMA_API}/prices-history?market={market_id}",
        ]
        for url in endpoints:
            try:
                resp = requests.get(url, params={'days': 30}, headers=HEADERS, timeout=6)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                records = data if isinstance(data, list) else data.get('history', data.get('prices', []))
                prices = []
                for r in records:
                    p = r.get('p', r.get('price', r.get('yes_price')))
                    if p is not None:
                        try:
                            prices.append(float(p))
                        except (ValueError, TypeError):
                            pass
                # Use price from mid-life (not the final 1.0 or 0.0)
                if len(prices) >= 4:
                    mid = len(prices) // 2
                    candidates = [p for p in prices[:mid] if 0.05 < p < 0.95]
                    if candidates:
                        return sum(candidates) / len(candidates)
            except Exception:
                pass
        return None  # no pre-resolution price found

    def _train(self) -> bool:
        try:
            from sklearn.ensemble import RandomForestClassifier
        except ImportError:
            logger.error("RF: scikit-learn not installed — run: pip install scikit-learn")
            return False

        logger.info("RF: fetching training data from Gamma API...")
        raw = self._fetch_resolved_markets(500)
        if not raw:
            logger.warning("RF: no resolved markets found — cannot train")
            return False

        X, y = [], []
        import json as _json

        for item in raw:
            try:
                market_id = str(item.get('id') or item.get('conditionId', ''))
                if not market_id:
                    continue

                # Determine label from outcome
                outcome_prices = item.get('outcomePrices', '["0.5","0.5"]')
                if isinstance(outcome_prices, str):
                    try:
                        outcome_prices = _json.loads(outcome_prices)
                    except Exception:
                        continue
                if not isinstance(outcome_prices, list) or len(outcome_prices) < 2:
                    continue

                try:
                    yes_final = float(outcome_prices[0])
                    no_final = float(outcome_prices[1])
                except (ValueError, TypeError):
                    continue

                if yes_final >= 0.95:
                    label = 1  # resolved YES
                elif no_final >= 0.95:
                    label = 0  # resolved NO
                else:
                    continue  # ambiguous resolution

                # Try to get pre-resolution price
                pre_price = self._fetch_price_history(market_id)
                if pre_price is None:
                    # Use a proxy: if high volume, slight YES lean; else use 0.5
                    vol = float(item.get('volumeNum') or item.get('volume') or 0)
                    pre_price = 0.55 if (vol > 50000 and label == 1) else 0.45 if (vol > 50000 and label == 0) else 0.5

                volume = float(item.get('volumeNum') or item.get('volume') or 0)
                liquidity = float(item.get('liquidityNum') or item.get('liquidity') or 0)
                end_date = item.get('endDate') or item.get('end_date_iso', '')

                features = [
                    pre_price,
                    _log1p(volume),
                    0.0,  # momentum unknown for training
                    0.0,  # days_to_expiry = 0 (resolved)
                    _log1p(liquidity),
                ]

                X.append(features)
                y.append(label)

            except Exception as e:
                logger.debug(f"RF: training sample error: {e}")
                continue

        if len(X) < 30:
            logger.warning(f"RF: only {len(X)} training samples — need 30+. Skipping.")
            return False

        X_arr = np.array(X)
        y_arr = np.array(y)

        clf = RandomForestClassifier(
            n_estimators=100,
            max_features='sqrt',   # √(n_features) per tree — as per article
            min_samples_leaf=3,
            random_state=42,
            n_jobs=-1,
        )
        clf.fit(X_arr, y_arr)

        self._model = clf
        self._trained_at = time.time()
        self._n_training_samples = len(X)

        yes_pct = sum(y_arr) / len(y_arr)
        logger.info(
            f"RF: trained on {len(X)} samples "
            f"(YES={yes_pct:.0%}, NO={1-yes_pct:.0%}). "
            f"Feature importances: {[round(v,3) for v in clf.feature_importances_]}"
        )
        return True

    def _ensure_trained(self) -> bool:
        now = time.time()
        if self._model and now - self._trained_at < self._retrain_interval:
            return True
        return self._train()

    # ------------------------------------------------------------------
    # Momentum helper
    # ------------------------------------------------------------------

    def _get_momentum(self, market_id: str, external_data: dict) -> float:
        try:
            from core.database import get_price_history
            if external_data and 'session' in external_data:
                history = get_price_history(external_data['session'], market_id, days=7)
                if len(history) >= 2:
                    prices = [h['yes_price'] for h in history]
                    return (prices[-1] - prices[0]) / max(prices[0], 0.01)
        except Exception:
            pass
        return 0.0

    # ------------------------------------------------------------------
    # Main interface
    # ------------------------------------------------------------------

    def calculate_probability(self, market, external_data=None) -> dict:
        market_id = market.market_id if hasattr(market, 'market_id') else str(market.get('id') or market.get('market_id', ''))
        yes_price = market.yes_price if hasattr(market, 'yes_price') else market.get('yes_price', 0.5)

        if not self._ensure_trained():
            # RF not ready yet — return market price with low conf
            return {
                'probability': yes_price,
                'confidence': 0.15,
                'method': 'rf_not_ready',
                'factors': {'samples': self._n_training_samples},
                'reasoning': f'RF model not trained yet ({self._n_training_samples} samples). Using market price.'
            }

        momentum = self._get_momentum(market_id, external_data or {})
        features = self._extract_features(market, momentum)

        proba = self._model.predict_proba([features])[0]
        prob_yes = float(proba[1])

        # Model confidence = how far from 0.5 (certainty)
        raw_conf = max(prob_yes, 1 - prob_yes)

        # Apply sigmoid (article: σ(x) = 1 / (1 + e^(-x)))
        # Map raw_conf from [0.5, 1.0] to a meaningful scale
        x = (raw_conf - 0.5) * 10  # scale to ~[-5, 5]
        sigma = 1 / (1 + math.exp(-x))
        confidence = float(np.clip(sigma * raw_conf, 0.10, 0.90))

        # Only surface high-confidence predictions (>=70%)
        if raw_conf < self.RF_MIN_CONF:
            return {
                'probability': yes_price,
                'confidence': 0.20,
                'method': 'rf_low_conf',
                'factors': {'rf_prob': round(prob_yes, 3), 'raw_conf': round(raw_conf, 3)},
                'reasoning': f'RF confidence {raw_conf:.0%} < 70% threshold. Using market price.'
            }

        # Check entry condition: market_price <= model_prob * 0.5
        entry_ok = yes_price <= prob_yes * self.RF_ENTRY_RATIO
        no_entry_ok = (1 - yes_price) <= (1 - prob_yes) * self.RF_ENTRY_RATIO

        logger.info(
            f"RF [{market_id[:8]}]: prob={prob_yes:.1%} mkt={yes_price:.1%} "
            f"entry_ok={entry_ok} conf={confidence:.0%} "
            f"momentum={momentum:+.2%}"
        )

        return {
            'probability': prob_yes,
            'confidence': confidence,
            'method': f'RandomForest(n={self._n_training_samples})',
            'factors': {
                'rf_prob_yes': round(prob_yes, 3),
                'yes_price': yes_price,
                'entry_ratio': self.RF_ENTRY_RATIO,
                'entry_ok': entry_ok,
                'momentum': round(momentum, 4),
                'raw_conf': round(raw_conf, 3),
            },
            'reasoning': (
                f'RF prob={prob_yes:.1%} vs market {yes_price:.1%}. '
                f'Entry {"✓" if entry_ok or no_entry_ok else "✗"} '
                f'(need price <= {prob_yes * self.RF_ENTRY_RATIO:.1%}). '
                f'Conf={confidence:.0%}. Momentum={momentum:+.1%}.'
            )
        }


# Singleton
_rf = None

def get_rf_model() -> RFModel:
    global _rf
    if _rf is None:
        _rf = RFModel()
    return _rf
