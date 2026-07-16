# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from pathlib import Path

import pytest
from pyoxigraph import DefaultGraph, Literal, NamedNode, Quad

import kgi.endpoints as endpoints


@pytest.fixture
def endpoint_factory():
    opened: list[endpoints.LocalSparqlGraphStore] = []

    def create(path: str) -> endpoints.LocalSparqlGraphStore:
        endpoint = endpoints.LocalSparqlGraphStore(path)
        opened.append(endpoint)
        return endpoint

    yield create

    for endpoint in opened:
        endpoint.close()


def _query_rows(
    endpoint: endpoints.LocalSparqlGraphStore, query: str
) -> list[dict[str, object]]:
    solutions = endpoint.query(query)
    variables = list(solutions.variables)
    return [
        {variable.value: solution[variable] for variable in variables}
        for solution in solutions
    ]


def test_local_endpoint_loads_without_blank_nodes(tmp_path, endpoint_factory) -> None:
    rdf_file = tmp_path / "data.nt"
    rdf_file.write_text('<http://example.com/s> <http://example.com/p> "o" .\n')

    endpoint = endpoint_factory(str(rdf_file))
    result = _query_rows(
        endpoint,
        "SELECT ?o WHERE { <http://example.com/s> <http://example.com/p> ?o }",
    )

    assert result == [{"o": Literal("o")}]


def test_local_endpoint_loads_rdf_in_exact_batches(monkeypatch, tmp_path) -> None:
    rdf_file = tmp_path / "data.nq"
    rdf_file.write_text(
        '<http://example.com/s1> <http://example.com/p> "o1" <http://example.com/g1> .\n'
        '<http://example.com/s2> <http://example.com/p> "o2" <http://example.com/g2> .\n'
        '<http://example.com/s3> <http://example.com/p> "o3" .\n'
    )

    class RecordingStore:
        def __init__(self) -> None:
            self.batches: list[list[Quad]] = []

        def bulk_extend(self, quads) -> None:
            self.batches.append(list(quads))

    store = RecordingStore()
    monkeypatch.setattr(endpoints, "_RDF_LOAD_BATCH_SIZE", 2)
    monkeypatch.setattr(endpoints, "Store", lambda path: store)

    endpoint = endpoints.LocalSparqlGraphStore(str(rdf_file))

    assert store.batches == [
        [
            Quad(
                NamedNode("http://example.com/s1"),
                NamedNode("http://example.com/p"),
                Literal("o1"),
                NamedNode("http://example.com/g1"),
            ),
            Quad(
                NamedNode("http://example.com/s1"),
                NamedNode("http://example.com/p"),
                Literal("o1"),
                DefaultGraph(),
            ),
        ],
        [
            Quad(
                NamedNode("http://example.com/s2"),
                NamedNode("http://example.com/p"),
                Literal("o2"),
                NamedNode("http://example.com/g2"),
            ),
            Quad(
                NamedNode("http://example.com/s2"),
                NamedNode("http://example.com/p"),
                Literal("o2"),
                DefaultGraph(),
            ),
        ],
        [
            Quad(
                NamedNode("http://example.com/s3"),
                NamedNode("http://example.com/p"),
                Literal("o3"),
                DefaultGraph(),
            )
        ],
    ]

    endpoint.close()


def test_local_endpoint_uses_temporary_disk_store(
    monkeypatch, tmp_path, endpoint_factory
) -> None:
    rdf_directory = tmp_path / "rdf"
    rdf_directory.mkdir()
    rdf_file = rdf_directory / "data.nt"
    rdf_file.write_text('<http://example.com/s> <http://example.com/p> "o" .\n')
    working_directory = tmp_path / "workspace"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)

    endpoint = endpoint_factory(str(rdf_file))
    store_path = Path(endpoint._temporary_directory.name)

    assert store_path.is_dir() is True
    assert store_path.parent == working_directory

    endpoint.close()

    assert store_path.exists() is False


def test_local_endpoint_preserves_explicit_blank_node_labels(
    tmp_path, endpoint_factory
) -> None:
    rdf_file = tmp_path / "data.nt"
    rdf_file.write_text('_:Student10 <http://example.com/p> "Venus" .\n')

    endpoint = endpoint_factory(str(rdf_file))
    result = _query_rows(
        endpoint,
        "SELECT ?value WHERE { "
        "?s <http://example.com/p> ?value "
        'FILTER(STR(?s) = "urn:bnode:Student10") '
        "}",
    )

    assert result == [{"value": Literal("Venus")}]


def test_local_endpoint_uses_native_parser_with_blank_nodes(
    monkeypatch, tmp_path, endpoint_factory
) -> None:
    rdf_file = tmp_path / "data.nt"
    rdf_file.write_text('_:Student10 <http://example.com/p> "Venus" .\n')
    parse_calls = []

    original_parse = endpoints.parse

    def parse_with_tracking(*, path, format):
        parse_calls.append((path, format))
        return original_parse(path=path, format=format)

    monkeypatch.setattr(endpoints, "parse", parse_with_tracking)

    endpoint = endpoint_factory(str(rdf_file))
    result = _query_rows(
        endpoint,
        "SELECT ?value WHERE { "
        "?s <http://example.com/p> ?value "
        'FILTER(STR(?s) = "urn:bnode:Student10") '
        "}",
    )

    assert result == [{"value": Literal("Venus")}]
    assert parse_calls == [(str(rdf_file), endpoints.RdfFormat.N_TRIPLES)]


def test_local_endpoint_converts_blank_node_objects(tmp_path, endpoint_factory) -> None:
    rdf_file = tmp_path / "data.nt"
    rdf_file.write_text("<http://example.com/s> <http://example.com/p> _:Object10 .\n")

    endpoint = endpoint_factory(str(rdf_file))
    result = _query_rows(
        endpoint,
        "SELECT ?object WHERE { "
        "<http://example.com/s> <http://example.com/p> ?object "
        'FILTER(STR(?object) = "urn:bnode:Object10") '
        "}",
    )

    assert result == [{"object": NamedNode("urn:bnode:Object10")}]


def test_local_endpoint_converts_blank_node_graph_names(
    tmp_path, endpoint_factory
) -> None:
    rdf_file = tmp_path / "data.nq"
    rdf_file.write_text(
        '<http://example.com/s> <http://example.com/p> "o" _:Graph10 .\n'
    )

    endpoint = endpoint_factory(str(rdf_file))
    result = _query_rows(
        endpoint,
        "SELECT ?value WHERE { "
        "GRAPH ?graph { <http://example.com/s> <http://example.com/p> ?value } "
        'FILTER(STR(?graph) = "urn:bnode:Graph10") '
        "}",
    )

    assert result == [{"value": Literal("o")}]


def test_local_endpoint_deduplicates_the_default_union(
    tmp_path, endpoint_factory
) -> None:
    rdf_file = tmp_path / "data.nq"
    rdf_file.write_text(
        '<http://example.com/s> <http://example.com/p> "o" <http://example.com/g1> .\n'
        '<http://example.com/s> <http://example.com/p> "o" <http://example.com/g2> .\n'
    )

    endpoint = endpoint_factory(str(rdf_file))
    default_rows = _query_rows(
        endpoint,
        "SELECT ?value WHERE { <http://example.com/s> <http://example.com/p> ?value }",
    )
    named_rows = _query_rows(
        endpoint,
        "SELECT ?graph WHERE { "
        'GRAPH ?graph { <http://example.com/s> <http://example.com/p> "o" } '
        "} ORDER BY ?graph",
    )

    assert default_rows == [{"value": Literal("o")}]
    assert named_rows == [
        {"graph": NamedNode("http://example.com/g1")},
        {"graph": NamedNode("http://example.com/g2")},
    ]


def test_detects_blank_node_marker_across_read_chunks(tmp_path) -> None:
    rdf_file = tmp_path / "data.nt"
    rdf_file.write_bytes((b"a" * ((1024 * 1024) - 1)) + b"_:")

    assert endpoints._has_explicit_blank_nodes(str(rdf_file)) is True


def test_local_endpoint_rejects_unsupported_rdf_extension(tmp_path) -> None:
    rdf_file = tmp_path / "data.rdfdata"
    rdf_file.write_text('<http://example.com/s> <http://example.com/p> "o" .\n')

    with pytest.raises(ValueError) as error:
        endpoints.LocalSparqlGraphStore(str(rdf_file))

    assert str(error.value) == f"Unsupported RDF file format: {rdf_file}"
