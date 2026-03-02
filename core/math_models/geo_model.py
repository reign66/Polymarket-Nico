import re
import math
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from .base_model import MathModel, ProbabilityResult

logger = logging.getLogger(__name__)


class GeoModel(MathModel):
    """Geopolitics model based on market momentum + CII scores.
    Confidence always LOW (0.15-0.40) because geopolitics is unpredictable."""

    def __init__(self, config: dict):
        super().__init__(config)
        self._cache: Dict = {}

    def _get_cii_scores(self) -> list:
        """Fetch CII (Conflict Intensity Index) scores from WorldMonitor.

        Returns a list of {region, score} dicts. Returns empty list on failure.
        Cached for 30 minutes.
        """
        cache_key = 'cii_scores'
        now = datetime.utcnow()
        if cache_key in self._cache:
            cached_data, cached_time = self._cache[cache_key]
            if (now - cached_time).total_seconds() < 1800:
                return cached_data

        try:
            url = 'https://worldmonitor.org/api/cii-scores'
            resp = requests.get(url, timeout=8)
            resp.raise_for_status()
            scores = resp.json()
            if isinstance(scores, list):
                self._cache[cache_key] = (scores, now)
                return scores
        except Exception as e:
            logger.warning(f"Could not fetch CII scores: {e}")

        self._cache[cache_key] = ([], now)
        return []

    def _calculate_momentum(self, price_history: list) -> float:
        """Linear regression slope of prices over the history.

        Returns slope (change per day). Positive = upward trend, negative = downward.
        Strong upward: slope > 0.02/day. Returns 0.0 if insufficient data.
        """
        if len(price_history) < 3:
            return 0.0

        n = len(price_history)
        x = list(range(n))
        mean_x = sum(x) / n
        mean_y = sum(price_history) / n

        numerator = sum((x[i] - mean_x) * (price_history[i] - mean_y) for i in range(n))
        denominator = sum((x[i] - mean_x) ** 2 for i in range(n))

        if denominator == 0:
            return 0.0

        slope = numerator / denominator
        return slope

    def _extract_country_or_region(self, question: str) -> Optional[str]:
        """Try to find country or region names in the question."""
        # Common geopolitical regions and countries
        regions = [
            'ukraine', 'russia', 'china', 'taiwan', 'iran', 'israel', 'gaza',
            'palestine', 'north korea', 'south korea', 'pakistan', 'india',
            'afghanistan', 'syria', 'iraq', 'turkey', 'saudi arabia', 'yemen',
            'ethiopia', 'sudan', 'venezuela', 'myanmar', 'nato', 'europe',
            'middle east', 'asia', 'africa', 'latin america', 'southeast asia',
        ]
        q_lower = question.lower()
        for region in sorted(regions, key=len, reverse=True):
            if region in q_lower:
                return region
        return None

    def _get_cii_for_region(self, region: str, cii_scores: list) -> float:
        """Find CII score for region. Returns 50 as neutral default."""
        if not cii_scores or not region:
            return 50.0

        region_lower = region.lower()
        for entry in cii_scores:
            if not isinstance(entry, dict):
                continue
            entry_region = str(entry.get('region', entry.get('country', ''))).lower()
            if region_lower in entry_region or entry_region in region_lower:
                try:
                    return float(entry.get('score', entry.get('cii', 50)))
                except (TypeError, ValueError):
                    return 50.0
        return 50.0

    def calculate_probability(self, market, external_data: dict = None) -> Optional[ProbabilityResult]:
        """Calculate geopolitical event probability using momentum + CII scores.

        This model is INTENTIONALLY weak. Only when the market is clearly mispriced
        (edge > 15%) will AI be called to confirm.
        """
        yes_price = getattr(market, 'yes_price', 0.5) or 0.5
        base_prob = float(yes_price)

        # Get price history for momentum
        price_history = []
        if external_data:
            price_history = external_data.get('price_history', [])

        momentum_slope = self._calculate_momentum(price_history)

        # Momentum adjustment (capped)
        momentum_adj = 0.0
        if momentum_slope > 0.02:
            # Strong upward trend
            momentum_adj = min(0.15, momentum_slope * 10)
        elif momentum_slope < -0.02:
            # Strong downward trend
            momentum_adj = max(-0.15, momentum_slope * 10)

        prob = base_prob + momentum_adj

        # CII adjustment
        cii_score = 50.0
        region = self._extract_country_or_region(getattr(market, 'question', '') or '')
        cii_adj = 0.0

        try:
            cii_scores = self._get_cii_scores()
            cii_score = self._get_cii_for_region(region, cii_scores)

            q_lower = (getattr(market, 'question', '') or '').lower()
            conflict_keywords = ['war', 'conflict', 'attack', 'invasion', 'military', 'crisis', 'tension']
            is_conflict_question = any(word in q_lower for word in conflict_keywords)

            if is_conflict_question:
                if cii_score > 70:
                    cii_adj = 0.10
                elif cii_score < 30:
                    cii_adj = -0.05
        except Exception as e:
            logger.warning(f"CII adjustment failed: {e}")

        prob = prob + cii_adj
        prob = max(0.05, min(0.95, prob))

        # Confidence: based on available data and momentum strength
        has_momentum_data = len(price_history) >= 5
        strong_momentum = abs(momentum_slope) > 0.02
        has_cii = cii_score != 50.0

        if has_momentum_data and strong_momentum and has_cii:
            confidence = 0.40
        elif has_momentum_data and strong_momentum:
            confidence = 0.35
        elif has_momentum_data:
            confidence = 0.25
        else:
            confidence = 0.15

        confidence = max(0.15, min(0.40, confidence))

        return ProbabilityResult(
            probability=prob,
            confidence=confidence,
            method='geo_momentum',
            factors={
                'base_prob': round(base_prob, 4),
                'momentum_slope': round(momentum_slope, 6),
                'momentum_adj': round(momentum_adj, 4),
                'cii_score': cii_score,
                'cii_adj': round(cii_adj, 4),
                'region': region,
                'history_points': len(price_history),
            },
            reasoning=(
                f"Geo momentum: base={base_prob:.1%}, momentum_adj={momentum_adj:+.1%}, "
                f"CII={cii_score:.0f} (adj={cii_adj:+.1%}). "
                f"Final={prob:.1%}, confidence={confidence:.0%}"
            )
        )
