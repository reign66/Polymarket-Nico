import math
import logging
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class EdgeResult:
    edge_yes: float            # model_prob - yes_price (positive = YES undervalued)
    edge_no: float             # (1 - model_prob) - no_price
    best_direction: str        # YES, NO, or SKIP
    best_edge: float           # max(edge_yes, edge_no) if positive
    confidence_adjusted_edge: float  # best_edge * model_confidence
    expected_value: float      # EV per dollar bet
    kelly_fraction: float      # Kelly optimal fraction
    should_call_ai: bool       # True if edge sufficient to warrant AI check
    factors: dict = field(default_factory=dict)

class EdgeCalculator:
    def __init__(self, config: dict):
        self.config = config
        self.filters = config.get('filters', {})
        self.kelly_config = config.get('kelly', {})

    def calculate_edge(self, market, model_result) -> EdgeResult:
        """Compare model probability with market prices to find edge.

        This is THE key function. It determines if we have a mathematical advantage.
        """
        model_prob = model_result.probability
        model_conf = model_result.confidence
        yes_price = market.yes_price
        no_price = market.no_price

        # Edge for each direction
        edge_yes = model_prob - yes_price          # positive = we think YES is more likely than market says
        edge_no = (1 - model_prob) - no_price      # positive = we think NO is more likely than market says

        # Determine best direction
        if edge_yes > edge_no and edge_yes > 0:
            best_direction = 'YES'
            best_edge = edge_yes
        elif edge_no > edge_yes and edge_no > 0:
            best_direction = 'NO'
            best_edge = edge_no
        else:
            best_direction = 'SKIP'
            best_edge = max(edge_yes, edge_no, 0)

        # Confidence-adjusted edge
        # An edge of 20% with confidence 0.2 = adjusted 4% (not enough)
        # An edge of 12% with confidence 0.8 = adjusted 9.6% (enough)
        confidence_adjusted_edge = best_edge * model_conf

        # Kelly Criterion
        kelly = 0.0
        expected_value = 0.0

        if best_direction == 'YES' and yes_price > 0:
            odds = (1.0 - yes_price) / yes_price
            p = model_prob
        elif best_direction == 'NO' and no_price > 0:
            odds = (1.0 - no_price) / no_price
            p = 1.0 - model_prob
        else:
            odds = 0
            p = 0

        if odds > 0 and p > 0:
            # Kelly formula: f* = (p * odds - (1-p)) / odds
            kelly_full = (p * odds - (1.0 - p)) / odds
            kelly_fraction = self.kelly_config.get('fraction', 0.25)
            max_bet_pct = self.kelly_config.get('max_bet_pct', 0.05)

            kelly = max(0, kelly_full * kelly_fraction)
            kelly = min(kelly, max_bet_pct)

            # Expected value per dollar bet
            expected_value = (p * odds) - (1.0 - p)

        # Should we call AI?
        min_math_edge = self.filters.get('min_math_edge', 0.08)
        should_call_ai = (
            confidence_adjusted_edge >= min_math_edge
            and kelly > 0
            and expected_value > 0
            and best_direction != 'SKIP'
        )

        result = EdgeResult(
            edge_yes=round(edge_yes, 4),
            edge_no=round(edge_no, 4),
            best_direction=best_direction,
            best_edge=round(best_edge, 4),
            confidence_adjusted_edge=round(confidence_adjusted_edge, 4),
            expected_value=round(expected_value, 4),
            kelly_fraction=round(kelly, 4),
            should_call_ai=should_call_ai,
            factors={
                'model_probability': round(model_prob, 4),
                'model_confidence': round(model_conf, 4),
                'yes_price': yes_price,
                'no_price': no_price,
                'method': model_result.method,
                'model_reasoning': model_result.reasoning
            }
        )

        logger.info(
            f"Edge calc [{market.market_id[:8]}]: "
            f"model={model_prob:.0%} conf={model_conf:.0%} | "
            f"edge_yes={edge_yes:+.1%} edge_no={edge_no:+.1%} | "
            f"adj_edge={confidence_adjusted_edge:.1%} | "
            f"dir={best_direction} kelly={kelly:.3f} EV={expected_value:.3f} | "
            f"call_ai={should_call_ai}"
        )

        return result
