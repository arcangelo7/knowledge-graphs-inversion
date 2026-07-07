# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

"""SPARQL endpoint implementations."""

import logging
import os

from io import BytesIO
from typing import TypeAlias

from pyoxigraph import (
    BlankNode,
    DefaultGraph,
    Literal,
    NamedNode,
    Quad,
    QueryResultsFormat,
    QuerySolutions,
    RdfFormat,
    Store,
    Triple,
    parse,
)

from kgi.base import Endpoint


_BNODE_IRI_PREFIX = "urn:bnode:"

RdfSubject: TypeAlias = NamedNode | BlankNode | Triple
RdfObject: TypeAlias = NamedNode | BlankNode | Literal | Triple
RdfGraphName: TypeAlias = NamedNode | BlankNode | DefaultGraph


def _subject_blank_nodes_to_iris(term: RdfSubject) -> NamedNode | Triple:
    if isinstance(term, BlankNode):
        return NamedNode(f"{_BNODE_IRI_PREFIX}{term.value}")
    if isinstance(term, Triple):
        return _triple_blank_nodes_to_iris(term)
    return term


def _object_blank_nodes_to_iris(term: RdfObject) -> NamedNode | Literal | Triple:
    if isinstance(term, BlankNode):
        return NamedNode(f"{_BNODE_IRI_PREFIX}{term.value}")
    if isinstance(term, Triple):
        return _triple_blank_nodes_to_iris(term)
    return term


def _graph_name_blank_nodes_to_iris(term: RdfGraphName) -> NamedNode | DefaultGraph:
    if isinstance(term, BlankNode):
        return NamedNode(f"{_BNODE_IRI_PREFIX}{term.value}")
    return term


def _triple_blank_nodes_to_iris(term: Triple) -> Triple:
    return Triple(
        _subject_blank_nodes_to_iris(term.subject),
        term.predicate,
        _object_blank_nodes_to_iris(term.object),
    )


def _load_preserving_blank_node_labels(
    store: Store, path: str, rdf_format: RdfFormat
) -> None:
    store.bulk_extend(
        Quad(
            _subject_blank_nodes_to_iris(quad.subject),
            quad.predicate,
            _object_blank_nodes_to_iris(quad.object),
            _graph_name_blank_nodes_to_iris(quad.graph_name),
        )
        for quad in parse(path=path, format=rdf_format)
    )


def _has_explicit_blank_nodes(path: str) -> bool:
    previous_tail = b""
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            if b"_:" in previous_tail + chunk:
                return True
            previous_tail = chunk[-1:]
    return False


def _rdf_format_from_path(path: str) -> RdfFormat | None:
    extension = os.path.splitext(path)[1].lstrip(".")
    if not extension:
        return None
    return RdfFormat.from_extension(extension)


class LocalSparqlGraphStore(Endpoint):
    """Local pyoxigraph-based SPARQL endpoint."""

    def __init__(self, url: str, delete_after_use: bool = False):
        self.delete_after_use = delete_after_use
        self._store: Store | None = Store()

        rdf_format = _rdf_format_from_path(url)
        if rdf_format is None:
            raise ValueError(f"Unsupported RDF file format: {url}")
        if not _has_explicit_blank_nodes(url):
            self._store.bulk_load(path=url, format=rdf_format)
        else:
            _load_preserving_blank_node_labels(self._store, url, rdf_format)

    def query(self, query: str):
        """Execute a SPARQL query on the local store and return SPARQL JSON."""
        assert self._store is not None
        try:
            results = self._store.query(query, use_default_graph_as_union=True)
            assert isinstance(results, QuerySolutions)
            buf = BytesIO()
            results.serialize(buf, QueryResultsFormat.JSON)
            return buf.getvalue().decode()
        except Exception as e:
            logging.getLogger("kgi").error(f"Query execution error: {e}")
            logging.getLogger("kgi").error(f"Failed query: {query}")
            raise

    def __del__(self):
        """Clean up resources."""
        if self.delete_after_use:
            self._store = None


class EndpointFactory:
    """Factory for creating SPARQL endpoints."""

    @classmethod
    def create_from_url(cls, url: str):
        """Create a local endpoint from a file path."""
        return LocalSparqlGraphStore(url)
