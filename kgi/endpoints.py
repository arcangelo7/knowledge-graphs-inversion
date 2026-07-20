# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

"""SPARQL endpoint implementations."""

import os
import tempfile
from collections.abc import Iterable, Iterator
from itertools import islice
from typing import TypeAlias, cast

from pyoxigraph import (
    BlankNode,
    DefaultGraph,
    Literal,
    NamedNode,
    Quad,
    QuerySolutions,
    RdfFormat,
    Store,
    Triple,
    parse,
)

from kgi.base import Endpoint


_BNODE_IRI_PREFIX = "urn:bnode:"
_RDF_LOAD_BATCH_SIZE = 100_000

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


def _quads_with_default_graph_union(
    path: str,
    rdf_format: RdfFormat,
    normalize_blank_nodes: bool,
) -> Iterator[Quad]:
    for quad in parse(path=path, format=rdf_format):
        normalized_quad = Quad(
            _subject_blank_nodes_to_iris(quad.subject)
            if normalize_blank_nodes
            else quad.subject,
            quad.predicate,
            _object_blank_nodes_to_iris(quad.object)
            if normalize_blank_nodes
            else quad.object,
            _graph_name_blank_nodes_to_iris(quad.graph_name)
            if normalize_blank_nodes
            else quad.graph_name,
        )
        yield normalized_quad
        if not isinstance(normalized_quad.graph_name, DefaultGraph):
            yield Quad(
                normalized_quad.subject,
                normalized_quad.predicate,
                normalized_quad.object,
                DefaultGraph(),
            )


def _bulk_extend_in_batches(store: Store, quads: Iterable[Quad]) -> None:
    iterator = iter(quads)
    while batch := list(islice(iterator, _RDF_LOAD_BATCH_SIZE)):
        store.bulk_extend(batch)


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

    def __init__(self, url: str):
        rdf_format = _rdf_format_from_path(url)
        if rdf_format is None:
            raise ValueError(f"Unsupported RDF file format: {url}")

        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix=".kgi_pyoxigraph_", dir=os.getcwd()
        )
        self._store: Store | None = Store(self._temporary_directory.name)
        loaded = False
        try:
            normalize_blank_nodes = _has_explicit_blank_nodes(url)
            _bulk_extend_in_batches(
                self._store,
                _quads_with_default_graph_union(url, rdf_format, normalize_blank_nodes),
            )
            loaded = True
        finally:
            if not loaded:
                self.close()

    def query(self, query: str) -> QuerySolutions:
        store = cast(Store, self._store)
        return cast(QuerySolutions, store.query(query))

    def close(self) -> None:
        self._store = None
        self._temporary_directory.cleanup()


class EndpointFactory:
    """Factory for creating SPARQL endpoints."""

    @classmethod
    def create_from_url(cls, url: str):
        """Create a local endpoint from a file path."""
        return LocalSparqlGraphStore(url)
