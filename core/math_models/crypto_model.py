"""GBM crypto model with CoinGecko data, regime detection, Fear & Greed."""

import numpy as np
from scipy.stats import norm
import requests
import time
import re
import datetime
import logging
from core.math_models.base_model import MathModel

logger = logging.getLogger(__name__)


class CryptoModel(MathModel):
    CRYPTO_MAP = {
        "bitcoin": "bitcoin", "btc": "bitcoin",
        "ethereum": "ethereum", "eth": "ethereum",
        "solana": "solana", "sol": "solana",
        "xrp": "ripple", "ripple": "ripple",
        "cardano": "cardano", "ada": "cardano",
        "dogecoin": "dogecoin", "doge": "dogecoin",
        "bnb": "binancecoin", "binance coin": "binancecoin",
        "avalanche": "avalanche-2", "avax": "avalanche-2",
        "polygon": "matic-network", "matic": "matic-network",
        "polkadot": "polkadot", "dot": "polkadot",
        "chainlink": "chainlink", "link": "chainlink",
        "litecoin": "litecoin", "ltc": "litecoin",
    }

    def __init__(self):
        self._cache = {}
        self._fng_cache = None
        self._fng_time = 0

    def _fetch_coingecko(self, coin_id: str, days: int = 90):
        cache_key = f"{coin_id}_{days}"
        if cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if time.time() - ts < 1800:
                return data
        try:
            url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
            r = requests.get(url, params={"vs_currency": "usd", "days": days}, timeout=15)
            r.raise_for_status()
            data = r.json()
            self._cache[cache_key] = (time.time(), data)
            return data
        except Exception as e:
            logger.warning(f"CoinGecko fetch failed for {coin_id}: {e}")
            return None

    def _fetch_fear_greed(self):
        if self._fng_cache and time.time() - self._fng_time < 21600:
            return self._fng_cache
        try:
            r = requests.get("https://api.alternative.me/fng/?limit=30", timeout=10)
            data = r.json().get('data', [])
            self._fng_cache = data
            self._fng_time = time.time()
            return data
        except Exception:
            return None

    def _detect_regime(self, prices):
        if len(prices) < 50:
            return "neutral", 1.0
        arr = np.array(prices)
        ma20 = np.mean(arr[-20:])
        ma50 = np.mean(arr[-50:])
        current = arr[-1]
        if len(prices) >= 200:
            ma200 = np.mean(arr[-200:])
            if current > ma20 > ma50 > ma200:
                return "strong_bull", 1.10   # was 1.30 — too aggressive
            elif current < ma20 < ma50 < ma200:
                return "strong_bear", 0.90   # was 0.70
        if current > ma20 > ma50:
            return "bull", 1.05              # was 1.15
        elif current < ma20 < ma50:
            return "bear", 0.95             # was 0.85
        return "neutral", 1.0

    def _parse_target(self, text: str):
        """Parse price target and direction (above/below) from question."""
        text_lower = text.lower()
        is_above = True
        target = None

        patterns = [
            r'\$\s*([\d,]+\.?\d*)\s*k\b',
            r'\$\s*([\d,]+\.?\d*)',
            r'([\d,]+\.?\d*)\s*(?:usd|dollars?)',
            r'(?:above|over|exceed|hit|reach|surpass)\s+\$?([\d,]+\.?\d*)',
            r'(?:below|under|drop|fall)\s+\$?([\d,]+\.?\d*)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text_lower)
            if match:
                val = match.group(1).replace(',', '')
                try:
                    target = float(val)
                except Exception:
                    continue
                end = match.end()
                if 'k' in text_lower[end:end+2]:
                    target *= 1000
                ctx = text_lower[max(0, match.start()-20):match.start()]
                if any(w in ctx for w in ['below', 'under', 'drop', 'fall']):
                    is_above = False
                break

        return target, is_above

    def calculate_probability(self, market, external_data=None) -> dict:
        question = market.question if hasattr(market, 'question') else market.get('question', '')
        description = market.description if hasattr(market, 'description') else market.get('description', '')
        text = f"{question} {description}".lower()

        # 1. Identify crypto
        coin_id = None
        for name, cg_id in self.CRYPTO_MAP.items():
            if name in text:
                coin_id = cg_id
                break
        if not coin_id:
            return self._fallback(market)

        # 2. Parse target price
        target, is_above = self._parse_target(text)
        if not target or target <= 0:
            return self._fallback(market)

        # 3. Parse end date
        end_date_str = (market.end_date if hasattr(market, 'end_date') else
                       market.get('end_date') or market.get('endDate'))
        target_date = None
        if end_date_str:
            try:
                if 'T' in str(end_date_str):
                    target_date = datetime.datetime.fromisoformat(
                        str(end_date_str).replace('Z', '+00:00'))
                else:
                    target_date = datetime.datetime.strptime(str(end_date_str), '%Y-%m-%d')
            except Exception:
                pass
        if not target_date:
            return self._fallback(market)

        now = datetime.datetime.now(datetime.timezone.utc)
        if target_date.tzinfo is None:
            target_date = target_date.replace(tzinfo=datetime.timezone.utc)
        days = (target_date - now).total_seconds() / 86400
        if days <= 0:
            return self._fallback(market)

        # 4. Fetch CoinGecko
        history = self._fetch_coingecko(coin_id, days=90)
        if not history or 'prices' not in history or len(history['prices']) < 10:
            return self._fallback(market)

        prices = [p[1] for p in history['prices']]
        current = prices[-1]

        # 5. GBM parameters — blended drift (40% short-term, 60% long-term)
        log_returns = np.diff(np.log(prices))
        if len(log_returns) < 5:
            return self._fallback(market)
        mu_90d = np.mean(log_returns)
        if len(log_returns) >= 30:
            mu_30d = np.mean(log_returns[-30:])
            mu_daily = 0.4 * mu_30d + 0.6 * mu_90d   # blend: more weight to long term
        else:
            mu_daily = mu_90d
        sigma_daily = max(np.std(log_returns), 0.001)

        # 6. Regime adjustment (mild — regime adjusts, not multiplies)
        regime, regime_factor = self._detect_regime(prices)
        mu_adj = mu_daily * regime_factor

        # Cap annual drift: no crypto reliably does >150%/year or <-80%/year
        MAX_ANNUAL = 1.50 / 365
        MIN_ANNUAL = -0.80 / 365
        mu_adj = max(MIN_ANNUAL, min(MAX_ANNUAL, mu_adj))

        # 7. Fear & Greed
        fng = self._fetch_fear_greed()
        fng_val = None
        if fng and len(fng) > 0:
            try:
                fng_val = int(fng[0].get('value', 50))
                if fng_val < 20:
                    mu_adj += 0.001
                elif fng_val > 80:
                    mu_adj -= 0.001
            except Exception:
                pass

        # 8. Volatility adjustment
        if days > 60:
            sigma_adj = sigma_daily * 0.9
        elif days < 7 and len(log_returns) >= 7:
            sigma_adj = np.std(log_returns[-7:])
        else:
            sigma_adj = sigma_daily
        sigma_adj = max(sigma_adj, 0.005)

        # 9. GBM analytical formula
        d = (np.log(target / current) - (mu_adj - sigma_adj**2 / 2) * days) \
            / (sigma_adj * np.sqrt(days))
        prob = 1 - norm.cdf(d)
        if not is_above:
            prob = 1 - prob
        prob = float(np.clip(prob, 0.02, 0.98))

        # 10. Confidence
        confidence = 0.40
        if len(prices) >= 90:
            confidence += 0.05
        if abs(current - target) / current < 0.15:
            confidence += 0.05
        if regime != "neutral":
            confidence += 0.05
        if fng_val is not None:
            confidence += 0.03
        confidence = min(confidence, 0.60)

        # 11. Divergence sanity check — if model diverges >40% from market, something is off
        market_price = market.yes_price if hasattr(market, 'yes_price') else market.get('yes_price', 0.5)
        divergence = abs(prob - float(market_price))
        if divergence > 0.40:
            penalty = max(0.3, 1.0 - (divergence - 0.40) * 2)
            confidence *= penalty
            logger.info(
                f"GBM high divergence {divergence:.0%} for '{question[:50]}' "
                f"(model={prob:.0%} market={float(market_price):.0%}) → conf→{confidence:.0%}"
            )

        return {
            'probability': prob,
            'confidence': confidence,
            'method': f'GBM+regime({regime})+FnG({fng_val})',
            'factors': {
                'coin': coin_id, 'current': round(current, 2),
                'target': target, 'days': round(days, 1),
                'mu': round(mu_adj, 6), 'sigma': round(sigma_adj, 4),
                'regime': regime, 'fng': fng_val, 'is_above': is_above,
            },
            'reasoning': (
                f'{coin_id} ${current:.0f}->${ target:.0f} in {days:.0f}d. '
                f'GBM={prob:.1%}. Regime={regime}. FnG={fng_val}.'
            )
        }
