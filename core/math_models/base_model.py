import logging
from abc import ABC, abstractmethod
from typing import Dict, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class ProbabilityResult:
    probability: float        # 0-1, our estimated probability
    confidence: float         # 0-1, how confident we are in our estimate
    method: str              # name of the method used
    factors: Dict = field(default_factory=dict)  # contributing factors
    reasoning: str = ""      # human-readable explanation

    def __post_init__(self):
        self.probability = max(0.01, min(0.99, self.probability))
        self.confidence = max(0.0, min(1.0, self.confidence))

class MathModel(ABC):
    """Abstract base for all mathematical probability models.

    The FUNDAMENTAL PRINCIPLE:
    Polymarket price = crowd's implied probability.
    Our model calculates an INDEPENDENT probability from data.
    The DIFFERENCE = potential edge.
    """

    def __init__(self, config: dict):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def calculate_probability(self, market, external_data: dict = None) -> Optional[ProbabilityResult]:
        """Calculate independent probability for this market.
        Returns None if the model can't handle this market."""
        pass

    def can_handle(self, market) -> bool:
        """Check if this model can handle the given market. Override in subclass."""
        return True
