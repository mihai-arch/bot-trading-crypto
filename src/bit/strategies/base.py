"""
BaseStrategy — the interface contract for all v1 trading strategies.

Every strategy must implement the `evaluate` method and expose a `strategy_id`.
The Protocol is runtime-checkable, allowing isinstance() checks in tests.

Adding a new strategy: create a new module in bit/strategies/, implement
BaseStrategy, and register the instance with SignalEngine at startup.
"""

from typing import Protocol, runtime_checkable

from ..domain.features import FeatureSet
from ..domain.signals import Signal


@runtime_checkable
class BaseStrategy(Protocol):
    strategy_id: str

    def evaluate(self, features: FeatureSet) -> Signal:
        """
        Evaluate the feature set and return a scored signal.

        Args:
            features: The full FeatureSet for the symbol at this moment.

        Returns:
            A Signal with:
            - score between 0.0 and 1.0
            - rationale explaining which conditions contributed to the score
            - metadata with per-condition debug values (optional but recommended)
        """
        ...
