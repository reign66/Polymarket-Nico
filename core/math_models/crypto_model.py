import re
import math
import logging
import requests
import numpy as np
from datetime import datetime
from typing import Optional, Tuple, Dict, Any
from scipy.stats import norm
from .base_model import MathModel, ProbabilityResult

logger = logging.getLogger(__name__)

COINGECKO_API = 'https://api.coingecko.com/api/v3'
HEADERS = {'User-Agent': 'PolymarketBot/2.0'}

CRYPTO_IDS = {
    'bitcoin': 'bitcoin', 'btc': 'bitcoin',
    'ethereum': 'ethereum', 'eth': 'ethereum',
    'solana': 'solana', 'sol': 'solana',
    'xrp': 'ripple', 'ripple': 'ripple',
    'cardano': 'cardano', 'ada': 'cardano',
    'dogecoin': 'dogecoin', 'doge': 'dogecoin',
    'polkadot': 'polkadot', 'dot': 'polkadot',
    'chainlink': 'chainlink', 'link': 'chainlink',
    'avalanche': 'avalanche-2', 'avax': 'avalanche-2',
    'polygon': 'matic-network', 'matic': 'matic-network',
}


class CryptoModel(MathModel):
    """Crypto price probability model using Geometric Brownian Motion."""

    def __init__(self, config: dict):
        super().__init__(config)
        self._cache: Dict[str, Tuple[Any, datetime]] = {}

    def _request(self, url: str, params: dict = None, cache_seconds: int = 300) -> Any:
        """Cached GET request with one retry."""
        cache_key = f"{url}:{str(params)}"
        now = datetime.utcnow()
        if cache_key in self._cache:
            cached_data, cached_time = self._cache[cache_key]
            if (now - cached_time).total_seconds() < cache_seconds:
                return cached_data

        for attempt in range(2):
            try:
                resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                self._cache[cache_key] = (data, now)
                return data
            except Exception as e:
                if attempt == 1:
                    logger.warning(f"Request failed for {url}: {e}")
                    raise
        return None

    def _get_coin_data(self, coin_id: str) -> Dict:
        """Fetch current market data for a coin from CoinGecko."""
        try:
            url = f'{COINGECKO_API}/coins/{coin_id}'
            params = {
                'localization': 'false',
                'tickers': 'false',
                'community_data': 'false',
                'developer_data': 'false',
            }
            data = self._request(url, params=params, cache_seconds=60)
            market_data = data.get('market_data', {})

            return {
                'price': market_data.get('current_price', {}).get('usd', 0),
                'market_cap': market_data.get('market_cap', {}).get('usd', 0),
                'volume_24h': market_data.get('total_volume', {}).get('usd', 0),
                'price_change_24h': market_data.get('price_change_percentage_24h', 0),
                'price_change_7d': market_data.get('price_change_percentage_7d', 0),
                'price_change_30d': market_data.get('price_change_percentage_30d', 0),
            }
        except Exception as e:
            logger.warning(f"Could not fetch coin data for {coin_id}: {e}")
            return {}

    def _get_price_history(self, coin_id: str, days: int = 90) -> list:
        """Fetch daily price history for a coin from CoinGecko."""
        try:
            url = f'{COINGECKO_API}/coins/{coin_id}/market_chart'
            params = {'vs_currency': 'usd', 'days': days}
            data = self._request(url, params=params, cache_seconds=3600)
            prices_raw = data.get('prices', [])
            # Each entry is [timestamp_ms, price]
            return [entry[1] for entry in prices_raw if len(entry) == 2]
        except Exception as e:
            logger.warning(f"Could not fetch price history for {coin_id}: {e}")
            return []

    def _calculate_gbm_probability(
        self,
        current_price: float,
        target_price: float,
        days: float,
        mu: float,
        sigma: float
    ) -> float:
        """Geometric Brownian Motion analytical probability of reaching target_price."""
        if days <= 0 or sigma <= 0 or current_price <= 0 or target_price <= 0:
            return 0.5
        d = (
            math.log(target_price / current_price) - (mu - sigma ** 2 / 2) * days
        ) / (sigma * math.sqrt(days))
        prob_above = 1 - norm.cdf(d)
        return max(0.02, min(0.98, prob_above))

    def _extract_price_target(self, question: str) -> Optional[Tuple[str, float, str]]:
        """Parse market question for coin + price target.

        Returns (coin_id, target_price, direction) where direction is 'above' or 'below'.
        Returns None if no target found.
        """
        q_lower = question.lower()

        # Find coin
        coin_id = None
        for alias in sorted(CRYPTO_IDS.keys(), key=len, reverse=True):
            if alias in q_lower:
                coin_id = CRYPTO_IDS[alias]
                break
        if not coin_id:
            return None

        # Find price target — patterns like $100,000 or $100k or 100000
        price_patterns = [
            r'\$\s*([\d,]+(?:\.\d+)?)\s*k\b',   # $100k
            r'\$\s*([\d,]+(?:\.\d+)?)',            # $100,000 or $100
            r'([\d,]+(?:\.\d+)?)\s*(?:usd|dollars)',
        ]

        target_price = None
        for pattern in price_patterns:
            match = re.search(pattern, q_lower)
            if match:
                raw = match.group(1).replace(',', '')
                try:
                    price = float(raw)
                    # Handle k suffix
                    if 'k' in match.group(0).lower():
                        price *= 1000
                    target_price = price
                    break
                except ValueError:
                    continue

        if target_price is None:
            return None

        # Determine direction
        above_words = ['above', 'over', 'exceed', 'hit', 'reach', 'break']
        below_words = ['below', 'under', 'drop', 'fall']
        direction = 'above'
        for word in below_words:
            if word in q_lower:
                direction = 'below'
                break

        return coin_id, target_price, direction

    def _extract_date_from_question(self, question: str, market_end_date: str) -> int:
        """Extract days remaining from question or market end_date."""
        now = datetime.utcnow()

        # Try to parse a date from the question
        date_patterns = [
            r'by\s+(\w+\s+\d{1,2},?\s+\d{4})',
            r'before\s+(\w+\s+\d{1,2},?\s+\d{4})',
            r'end of\s+(\w+\s+\d{4})',
        ]
        month_map = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12,
            'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6,
            'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
        }

        q_lower = question.lower()
        for pattern in date_patterns:
            match = re.search(pattern, q_lower)
            if match:
                date_str = match.group(1).strip()
                try:
                    for fmt in ['%B %d, %Y', '%B %d %Y', '%B %Y']:
                        try:
                            parsed = datetime.strptime(date_str.title(), fmt)
                            days = (parsed - now).days
                            return max(1, days)
                        except ValueError:
                            continue
                except Exception:
                    pass

        # Fall back to market end date
        if market_end_date:
            for fmt in ['%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S']:
                try:
                    end = datetime.strptime(market_end_date, fmt)
                    days = (end - now).days
                    return max(1, days)
                except ValueError:
                    continue

        return 30  # default fallback

    def _calculate_volatility(self, prices: list) -> Tuple[float, float]:
        """Compute (mu, sigma) from daily price history using log returns."""
        if len(prices) < 10:
            return 0.0, 0.5  # high uncertainty default

        log_returns = [
            math.log(prices[i] / prices[i - 1])
            for i in range(1, len(prices))
            if prices[i - 1] > 0 and prices[i] > 0
        ]
        if not log_returns:
            return 0.0, 0.5

        mu = float(np.mean(log_returns))     # daily mean return
        sigma = float(np.std(log_returns))   # daily volatility
        return mu, sigma

    def calculate_probability(self, market, external_data: dict = None) -> Optional[ProbabilityResult]:
        """Calculate crypto price probability using GBM or fall back to market prior."""
        question = getattr(market, 'question', '') or ''
        end_date = getattr(market, 'end_date_iso', None) or getattr(market, 'end_date', None) or ''

        extracted = self._extract_price_target(question)

        if not extracted:
            # Binary event (ETF approval etc.): trust market price, very low confidence
            market_price = getattr(market, 'yes_price', 0.5) or 0.5
            return ProbabilityResult(
                probability=market_price,
                confidence=0.15,
                method='market_prior',
                factors={'market_price': market_price, 'reason': 'no_price_target'},
                reasoning="No price target found; trusting market price as prior. Confidence very low."
            )

        coin_id, target_price, direction = extracted

        coin_data = self._get_coin_data(coin_id)
        current_price = coin_data.get('price', 0)
        if not current_price or current_price <= 0:
            return None

        history = self._get_price_history(coin_id, days=90)
        mu, sigma = self._calculate_volatility(history)

        # Volatility adjustment: if recent 24h change implies spike, increase sigma
        if history and len(history) >= 10:
            recent_history = history[-7:]  # last 7 days
            _, recent_sigma = self._calculate_volatility(recent_history)
            if recent_sigma > 2 * sigma:
                sigma *= 1.2

        days = self._extract_date_from_question(question, str(end_date))
        prob_above = self._calculate_gbm_probability(current_price, target_price, days, mu, sigma)
        probability = prob_above if direction == 'above' else 1.0 - prob_above

        # Confidence: higher for longer timeframes (more data predictive power), lower for short
        if days >= 180:
            confidence = 0.70
        elif days >= 60:
            confidence = 0.65
        elif days >= 14:
            confidence = 0.60
        else:
            confidence = 0.50

        return ProbabilityResult(
            probability=probability,
            confidence=confidence,
            method='gbm',
            factors={
                'coin': coin_id,
                'current_price': current_price,
                'target_price': target_price,
                'direction': direction,
                'mu_daily': round(mu, 6),
                'sigma_daily': round(sigma, 6),
                'days': days,
                'annualized_vol': round(sigma * math.sqrt(252), 4),
            },
            reasoning=(
                f"GBM model: {coin_id} current=${current_price:,.2f}, target=${target_price:,.2f} "
                f"({direction}), {days} days. "
                f"Daily vol={sigma:.3f}, drift={mu:.5f}. "
                f"Probability: {probability:.1%}"
            )
        )

    def can_handle(self, market) -> bool:
        """True if a known crypto coin is found in the market question."""
        question = getattr(market, 'question', '') or ''
        q_lower = question.lower()
        return any(alias in q_lower for alias in CRYPTO_IDS)
