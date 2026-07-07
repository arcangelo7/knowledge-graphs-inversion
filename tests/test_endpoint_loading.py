# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import json

import pytest

import kgi.endpoints as endpoints


def test_local_endpoint_uses_native_loader_without_blank_nodes(tmp_path) -> None:
    rdf_file = tmp_path / "data.nt"
    rdf_file.write_text('<http://example.com/s> <http://example.com/p> "o" .\n')

    endpoint = endpoints.LocalSparqlGraphStore(str(rdf_file))
    result = json.loads(
        endpoint.query(
            "SELECT ?o WHERE { <http://example.com/s> <http://example.com/p> ?o }"
        )
    )

    assert result == {
        "head": {"vars": ["o"]},
        "results": {
            "bindings": [
                {
                    "o": {
                        "type": "literal",
                        "value": "o",
                    }
                }
            ]
        },
    }


def test_local_endpoint_preserves_explicit_blank_node_labels(tmp_path) -> None:
    rdf_file = tmp_path / "data.nt"
    rdf_file.write_text('_:Student10 <http://example.com/p> "Venus" .\n')

    endpoint = endpoints.LocalSparqlGraphStore(str(rdf_file))
    result = json.loads(
        endpoint.query(
            "SELECT ?value WHERE { "
            "?s <http://example.com/p> ?value "
            'FILTER(STR(?s) = "urn:bnode:Student10") '
            "}"
        )
    )

    assert result == {
        "head": {"vars": ["value"]},
        "results": {
            "bindings": [
                {
                    "value": {
                        "type": "literal",
                        "value": "Venus",
                    }
                }
            ]
        },
    }


def test_local_endpoint_uses_native_parser_with_blank_nodes(
    monkeypatch, tmp_path
) -> None:
    rdf_file = tmp_path / "data.nt"
    rdf_file.write_text('_:Student10 <http://example.com/p> "Venus" .\n')
    parse_calls = []

    original_parse = endpoints.parse

    def parse_with_tracking(*, path, format):
        parse_calls.append((path, format))
        return original_parse(path=path, format=format)

    monkeypatch.setattr(endpoints, "parse", parse_with_tracking)

    endpoint = endpoints.LocalSparqlGraphStore(str(rdf_file))
    result = json.loads(
        endpoint.query(
            "SELECT ?value WHERE { "
            "?s <http://example.com/p> ?value "
            'FILTER(STR(?s) = "urn:bnode:Student10") '
            "}"
        )
    )

    assert result == {
        "head": {"vars": ["value"]},
        "results": {
            "bindings": [
                {
                    "value": {
                        "type": "literal",
                        "value": "Venus",
                    }
                }
            ]
        },
    }
    assert parse_calls == [(str(rdf_file), endpoints.RdfFormat.N_TRIPLES)]


def test_local_endpoint_converts_blank_node_objects(tmp_path) -> None:
    rdf_file = tmp_path / "data.nt"
    rdf_file.write_text("<http://example.com/s> <http://example.com/p> _:Object10 .\n")

    endpoint = endpoints.LocalSparqlGraphStore(str(rdf_file))
    result = json.loads(
        endpoint.query(
            "SELECT ?object WHERE { "
            "<http://example.com/s> <http://example.com/p> ?object "
            'FILTER(STR(?object) = "urn:bnode:Object10") '
            "}"
        )
    )

    assert result == {
        "head": {"vars": ["object"]},
        "results": {
            "bindings": [
                {
                    "object": {
                        "type": "uri",
                        "value": "urn:bnode:Object10",
                    }
                }
            ]
        },
    }


def test_local_endpoint_converts_blank_node_graph_names(tmp_path) -> None:
    rdf_file = tmp_path / "data.nq"
    rdf_file.write_text(
        '<http://example.com/s> <http://example.com/p> "o" _:Graph10 .\n'
    )

    endpoint = endpoints.LocalSparqlGraphStore(str(rdf_file))
    result = json.loads(
        endpoint.query(
            "SELECT ?value WHERE { "
            "GRAPH ?graph { <http://example.com/s> <http://example.com/p> ?value } "
            'FILTER(STR(?graph) = "urn:bnode:Graph10") '
            "}"
        )
    )

    assert result == {
        "head": {"vars": ["value"]},
        "results": {
            "bindings": [
                {
                    "value": {
                        "type": "literal",
                        "value": "o",
                    }
                }
            ]
        },
    }


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
