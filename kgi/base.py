"""Abstract base classes for the KGI library."""

from abc import ABC, abstractmethod
from typing import Self

import pandas as pd


class Endpoint(ABC):
    """Abstract base class for SPARQL endpoints."""
    
    @abstractmethod
    def query(self, query: str):
        """Execute a SPARQL query and return results."""
        raise NotImplementedError


class Triple(ABC):
    """Abstract base class for RDF triples."""
    
    @abstractmethod
    def generate(self) -> str:
        """Generate the string representation of the triple."""
        raise NotImplementedError


class Node(ABC):
    """Abstract base class for JSON template nodes."""
    
    def find(self, key: str) -> Self | None:
        """Find a child node by key."""
        raise NotImplementedError
    
    @property
    @abstractmethod
    def path(self) -> str:
        """Get the path of this node."""
        raise NotImplementedError
    
    @property
    @abstractmethod
    def parent_path(self) -> str:
        """Get the parent path of this node."""
        raise NotImplementedError
        
    @abstractmethod
    def to_template(self) -> str:
        """Convert node to template string."""
        raise NotImplementedError
    
    @abstractmethod
    def fill(self, data: pd.DataFrame) -> str:
        """Fill the node with actual data."""
        raise NotImplementedError


class Template(ABC):
    """Abstract base class for data templates."""
    
    @abstractmethod
    def create_template(self) -> str:
        """Create a template structure."""
        raise NotImplementedError
    
    @abstractmethod
    def fill_data(self, data: pd.DataFrame, source_name: str) -> str:
        """Fill template with data."""
        raise NotImplementedError