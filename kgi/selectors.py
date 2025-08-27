"""Triple selection strategies."""

from .base import Selector, Triple
from .constants import QUERY_MINIMAL, QUERY_REDUCED, QUERY_FULL


class MinimalSelector(Selector):
    """Minimal triple selection strategy."""
    
    def select(self, triples: list[Triple]) -> list[Triple]:
        """Select minimal set of triples."""
        raise NotImplementedError("MinimalSelector not yet implemented")

    def __str__(self):
        return "MinimalSelector"


class ReducedSelector(Selector):
    """Reduced triple selection strategy."""
    
    def select(self, triples: list[Triple]) -> list[Triple]:
        """Select reduced set of triples."""
        raise NotImplementedError("ReducedSelector not yet implemented")

    def __str__(self):
        return "ReducedSelector"


class FullSelector(Selector):
    """Full triple selection strategy - includes all triples."""
    
    def select(self, triples: list[Triple]) -> list[Triple]:
        """Select all triples."""
        return triples

    def __str__(self):
        return "FullSelector"


class SelectorFactory:
    """Factory for creating selectors."""
    
    @staticmethod
    def create(selector: int) -> Selector:
        """Create a selector based on type."""
        if selector == QUERY_MINIMAL:
            return MinimalSelector()
        elif selector == QUERY_REDUCED:
            return ReducedSelector()
        elif selector == QUERY_FULL:
            return FullSelector()
        else:
            raise ValueError(f"Unknown selector: {selector}")