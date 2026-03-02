import re
import logging
from typing import Optional, List
from .base_model import MathModel, ProbabilityResult

logger = logging.getLogger(__name__)


class PoliticsModel(MathModel):
    """Politics model using market momentum + simple heuristics.
    Similar to GeoModel: confidence 0.20-0.45 max."""

    INCUMBENCY_ADVANTAGE = 0.55  # Historical baseline for incumbents winning

    # Keywords that suggest an incumbent is referenced
    INCUMBENT_KEYWORDS = [
        'incumbent', 'current', 'sitting', 'in office', 'president', 'senator',
        'governor', 'mayor', 'representative', 'congressman', 'congresswoman',
        're-election', 'reelection', 're-elect', 'defend', 'retain',
    ]

    # Known current officeholders (to help detect incumbency in market questions)
    KNOWN_INCUMBENTS = [
        'biden', 'trump', 'harris', 'obama',
        'macron', 'scholz', 'sunak', 'starmer',
    ]

    def _detect_incumbent(self, question: str) -> bool:
        """Check if question mentions current office holder or incumbency."""
        q_lower = question.lower()
        for keyword in self.INCUMBENT_KEYWORDS:
            if keyword in q_lower:
                return True
        for name in self.KNOWN_INCUMBENTS:
            if name in q_lower:
                return True
        return False

    def _calculate_momentum(self, price_history: list) -> float:
        """Linear regression slope of prices over the history.

        Returns slope per day. Positive = upward trend, negative = downward.
        Returns 0.0 if insufficient data.
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

        return numerator / denominator

    def _detect_election_type(self, question: str) -> str:
        """Classify the election type from the market question."""
        q_lower = question.lower()

        presidential_keywords = [
            'president', 'presidential', 'white house', 'oval office',
            'commander in chief', 'head of state', 'prime minister',
        ]
        congressional_keywords = [
            'senate', 'senator', 'congress', 'representative', 'house of representatives',
            'parliament', 'mp', 'legislat',
        ]
        state_keywords = [
            'governor', 'state', 'mayor', 'city council', 'municipal',
            'county', 'district', 'local',
        ]

        for keyword in presidential_keywords:
            if keyword in q_lower:
                return 'presidential'
        for keyword in congressional_keywords:
            if keyword in q_lower:
                return 'congressional'
        for keyword in state_keywords:
            if keyword in q_lower:
                return 'state'

        return 'other'

    def calculate_probability(self, market, external_data: dict = None) -> Optional[ProbabilityResult]:
        """Calculate political event probability using market momentum + heuristics."""
        yes_price = getattr(market, 'yes_price', 0.5) or 0.5
        base_prob = float(yes_price)
        question = getattr(market, 'question', '') or ''

        # Get price history for momentum
        price_history = []
        if external_data:
            price_history = external_data.get('price_history', [])

        momentum_slope = self._calculate_momentum(price_history)

        # Incumbency adjustment
        incumbency_adj = 0.0
        is_incumbent = self._detect_incumbent(question)
        if is_incumbent:
            # Slight pull toward historical incumbency advantage
            target = self.INCUMBENCY_ADVANTAGE
            incumbency_adj = (target - base_prob) * 0.15  # gentle nudge

        # Momentum adjustment (capped at 0.10)
        momentum_adj = 0.0
        if momentum_slope > 0.01:
            momentum_adj = min(0.10, momentum_slope * 8)
        elif momentum_slope < -0.01:
            momentum_adj = max(-0.10, momentum_slope * 8)

        election_type = self._detect_election_type(question)

        # Election type weight: presidential markets tend to be more efficient
        efficiency_discount = {
            'presidential': 0.85,
            'congressional': 0.90,
            'state': 0.95,
            'other': 1.0,
        }.get(election_type, 1.0)

        # Blend adjustments with efficiency discount
        prob = base_prob + (incumbency_adj + momentum_adj) * efficiency_discount
        prob = max(0.05, min(0.95, prob))

        # Confidence determination
        has_history = len(price_history) >= 5
        strong_momentum = abs(momentum_slope) > 0.01
        large_incumbent_signal = is_incumbent and abs(base_prob - 0.5) > 0.15

        if has_history and strong_momentum and large_incumbent_signal:
            confidence = 0.45
        elif has_history and (strong_momentum or large_incumbent_signal):
            confidence = 0.35
        elif has_history or is_incumbent:
            confidence = 0.28
        else:
            confidence = 0.20

        confidence = max(0.20, min(0.45, confidence))

        return ProbabilityResult(
            probability=prob,
            confidence=confidence,
            method='political_momentum',
            factors={
                'base_prob': round(base_prob, 4),
                'incumbency_adj': round(incumbency_adj, 4),
                'momentum_slope': round(momentum_slope, 6),
                'momentum_adj': round(momentum_adj, 4),
                'election_type': election_type,
                'is_incumbent': is_incumbent,
                'efficiency_discount': efficiency_discount,
                'history_points': len(price_history),
            },
            reasoning=(
                f"Political model: base={base_prob:.1%}, "
                f"incumbency_adj={incumbency_adj:+.1%}, "
                f"momentum_adj={momentum_adj:+.1%}. "
                f"Election type: {election_type}. "
                f"Final={prob:.1%}, confidence={confidence:.0%}"
            )
        )
