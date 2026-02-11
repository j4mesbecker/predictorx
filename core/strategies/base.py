"""
PredictorX — Abstract Strategy Interface
All prediction strategies implement this interface.
"""

from abc import ABC, abstractmethod
from core.models import Prediction


class Strategy(ABC):
    """Base class for all prediction strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy identifier (e.g., 'weather', 'sp_tail')."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description."""
        ...

    @abstractmethod
    async def scan(self) -> list[Prediction]:
        """Scan markets and return predictions with edge and confidence."""
        ...

    @abstractmethod
    async def get_confidence_factors(self, prediction: Prediction) -> dict:
        """Return dict of factors contributing to confidence score."""
        ...

    async def is_available(self) -> bool:
        """Check if this strategy can run (dependencies available)."""
        return True
