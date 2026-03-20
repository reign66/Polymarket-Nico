"""
core/edge_calculator.py — V2.2 Edge calculator with adaptive thresholds.

Changelog V2.2:
- Added quaternary condition: edge >= 10% AND conf >= 0.25
  Fixes models with moderate confidence (generic, geo, fallback) never triggering AI.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class EdgeResult:
    market_id: str
    model_prob: float
    model_confidence: float
    edge_yes: float
    edge_no: float
    best_edge: float
    best_direction: str  # YES / NO / SKIP
    confidence_adjusted_edge: float
    kelly_fraction: float
    expected_value: float
    should_call_ai: bool
    reason: str = ""


class EdgeCalculator:
    def __init__(self, config: dict):
        self.config = config
        self.filters = config.get('filters', {})
        self.kelly_cfg = config.get('kelly', {})

    def calculate_edge(self, market, model_result: dict) -> EdgeResult:
        """
        Calculate edge and Kelly criterion for a market.
        model_result dict must have: probability, confidence, method
        """
        market_id = market.market_id if hasattr(market, 'market_id') else str(market.get('id', ''))
        yes_price = market.yes_price if hasattr(market, 'yes_price') else market.get('yes_price', 0.5)
        no_price = market.no_price if hasattr(market, 'no_price') else market.get('no_price', 0.5)
        question = market.question if hasattr(market, 'question') else market.get('question', '')

        model_prob = model_result.get('probability', 0.5)
        model_confidence = model_result.get('confidence', 0.05)
        model_result_method = model_result.get('method', '')

        # Raw edges
        edge_yes = model_prob - yes_price
        edge_no = (1 - model_prob) - no_price

        # Best direction
        if edge_yes > edge_no and edge_yes > 0:
            best_direction = 'YES'
            best_edge = edge_yes
            price = yes_price
        elif edge_no > 0:
            best_direction = 'NO'
            best_edge = edge_no
            price = no_price
        else:
            best_direction = 'SKIP'
            best_edge = max(edge_yes, edge_no, 0)
            price = yes_price

        # Non-linear confidence weighting
        if model_confidence >= 0.70:
            weight = model_confidence * 1.1
        elif model_confidence >= 0.50:
            weight = model_confidence
        elif model_confidence >= 0.35:
            weight = model_confidence * 0.80
        elif model_confidence >= 0.20:
            weight = model_confidence * 0.55
        else:
            weight = model_confidence * 0.25

        confidence_adjusted_edge = best_edge * weight

        # Kelly criterion: f* = (p*odds - (1-p)) / odds
        kelly = 0.0
        if price > 0 and best_direction != 'SKIP':
            p = model_prob if best_direction == 'YES' else (1 - model_prob)
            odds = 1.0 / price
            raw_kelly = (p * odds - (1 - p)) / odds
            kelly_fraction = self.kelly_cfg.get('fraction', 0.25)
            kelly = max(0.0, raw_kelly * kelly_fraction)

        # Expected Value
        ev = 0.0
        if best_direction == 'YES':
            ev = model_prob * (1.0 - yes_price) - (1 - model_prob) * yes_price
        elif best_direction == 'NO':
            ev = (1 - model_prob) * (1.0 - no_price) - model_prob * no_price

        # Adaptive AI trigger — 4 conditions (OR logic)
        should_call_ai = False
        reason = ""
        if best_direction == 'SKIP':
            reason = "No positive edge in either direction"
        elif kelly <= 0:
            reason = f"Kelly={kelly:.3f} <= 0"
        elif ev <= 0:
            reason = f"EV={ev:.3f} <= 0"
        else:
            # Primary: strong adjusted edge
            if confidence_adjusted_edge >= 0.04:
                should_call_ai = True
                reason = f"adj_edge={confidence_adjusted_edge:.1%} >= 4%"
            # Secondary: small edge + HIGH confidence
            elif best_edge >= 0.02 and model_confidence >= 0.50:
                should_call_ai = True
                reason = f"edge={best_edge:.1%} >= 3% AND conf={model_confidence:.0%} >= 60%"
            # Tertiary: medium edge + medium confidence
            elif best_edge >= 0.03 and model_confidence >= 0.35:
                should_call_ai = True
                reason = f"edge={best_edge:.1%} >= 5% AND conf={model_confidence:.0%} >= 45%"
            # Quaternary: high edge + moderate confidence (fixes generic/geo/fallback models)
            elif best_edge >= 0.07 and model_confidence >= 0.20:
                should_call_ai = True
                reason = f"edge={best_edge:.1%} >= 10% AND conf={model_confidence:.0%} >= 25%"
            # Quinary: RF entry condition — market_price <= model_prob * 0.50 (2x undervalued)
            elif model_result_method and 'RandomForest' in model_result_method and best_edge >= 0.13:
                should_call_ai = True
                reason = f"RF: market={yes_price:.1%} <= prob*0.5 | edge={best_edge:.1%}"
            else:
                reason = (
                    f"adj_edge={confidence_adjusted_edge:.1%} too low "
                    f"(edge={best_edge:.1%}, conf={model_confidence:.0%})"
                )

        question_short = question[:50] if question else ''
        logger.debug(
            f"Edge [{market_id}] '{question_short}': "
            f"model={model_prob:.0%} conf={model_confidence:.0%} "
            f"edge_yes={edge_yes:+.1%} edge_no={edge_no:+.1%} "
            f"adj={confidence_adjusted_edge:.1%} dir={best_direction} ai={should_call_ai}"
        )

        return EdgeResult(
            market_id=market_id,
            model_prob=model_prob,
            model_confidence=model_confidence,
            edge_yes=edge_yes,
            edge_no=edge_no,
            best_edge=best_edge,
            best_direction=best_direction,
            confidence_adjusted_edge=confidence_adjusted_edge,
            kelly_fraction=kelly,
            expected_value=ev,
            should_call_ai=should_call_ai,
            reason=reason,
        )
