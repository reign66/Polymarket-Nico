import math
import logging
from typing import Optional
from .base_model import MathModel, ProbabilityResult

logger = logging.getLogger(__name__)


class GenericModel(MathModel):
    """Fallback model for markets that don't match any specialized model.
    Uses pure market analysis. Very low confidence."""

    def calculate_probability(self, market, external_data: dict = None) -> Optional[ProbabilityResult]:
        """Calculate probability by trusting market efficiency.

        For unrecognized markets, the crowd price is the best prior.
        Confidence is intentionally very low so this almost never triggers AI calls.
        """
        volume = getattr(market, 'volume', 0) or 0
        liquidity = getattr(market, 'liquidity', 0) or 0

        # Start with market price (trust the crowd for unknowns)
        probability = getattr(market, 'yes_price', 0.5) or 0.5

        # Price extremity factor: prices near 0.5 have more movement potential
        extremity = abs(probability - 0.5)  # 0 = 50/50, 0.5 = extreme

        # For very liquid markets, trust the price more (less edge likely)
        efficiency = min(1.0, (volume * liquidity) / 1e9) if volume > 0 else 0

        # Confidence is VERY low — this model almost never generates actionable signals
        confidence = 0.10 * (1 - efficiency)  # More efficient = even less confident we can beat it
        confidence = max(0.05, min(0.15, confidence))

        return ProbabilityResult(
            probability=probability,
            confidence=confidence,
            method='generic_market',
            factors={
                'volume': volume,
                'liquidity': liquidity,
                'efficiency': round(efficiency, 3),
                'extremity': round(extremity, 3),
            },
            reasoning=(
                f"Generic model: trusting market price ({probability:.0%}), "
                f"confidence very low ({confidence:.0%})"
            )
        )
