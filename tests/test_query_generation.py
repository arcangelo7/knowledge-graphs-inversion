# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import pandas as pd
import pytest
from pyoxigraph import Literal, NamedNode, Quad, QuerySolutions, Store

from kgi.constants import (
    RML_BLANK_NODE,
    RML_IRI,
    RML_LITERAL,
    RML_REFERENCE,
    RML_TEMPLATE,
)
from kgi.core import _check_for_ambiguous_subject_templates
from kgi.exceptions import NonInvertibleError
from kgi.query import Query, _select_query_source_rules, _solutions_to_dataframes
from kgi.schema import ColumnInfo, infer_type_from_value_with_schema
from kgi.triples import QueryTriple, SubjectTriple
from kgi.utils import Codex, IdGenerator, insert_columns, sparql_to_python_type


def _rule(subject_column: str, object_column: str) -> dict[str, object]:
    return {
        "source_type": "RDB",
        "logical_source_value": "data",
        "logical_source_type": "http://www.w3.org/ns/r2rml#tableName",
        "subject_map_type": RML_TEMPLATE,
        "subject_map_value": f"http://example.com/table/{{{subject_column}}}",
        "subject_termtype": RML_IRI,
        "predicate_map_type": "http://w3id.org/rml/constant",
        "predicate_map_value": "http://example.com/p1",
        "object_map_type": RML_REFERENCE,
        "object_map_value": object_column,
        "object_termtype": RML_LITERAL,
        "lang_datatype": None,
        "lang_datatype_map_type": None,
        "lang_datatype_map_value": None,
        "object_join_conditions": None,
        "graph_map_type": None,
        "graph_map_value": None,
        "triples_map_id": f"TriplesMap_{subject_column}",
    }


def _two_column_rule(subject_template: str, object_column: str) -> dict[str, object]:
    rule = _rule("p1", object_column)
    rule["subject_map_value"] = subject_template
    return rule


def test_subject_template_adds_filter_when_reference_is_already_bound() -> None:
    mappings = pd.DataFrame([_rule("p1", "p1")])
    insert_columns(mappings)
    rule = mappings.iloc[0]
    query = Query([QueryTriple(rule), SubjectTriple(rule)])

    generated = query.generate(mappings)

    assert generated == (
        "SELECT ?p1 WHERE {?p1_uri <http://example.com/p1> ?p1 .\n"
        "FILTER(REGEX(STR(?p1_uri), 'http://example.com/table/([^/]*)'))\n"
        "BIND(STRAFTER(STR(?p1_uri), 'http://example.com/table/') as ?p1_uri_slice)\n"
        "FILTER(!BOUND(?p1) || STR(?p1) = STR(?p1_uri_slice) "
        "|| ENCODE_FOR_URI(STR(?p1)) = STR(?p1_uri_slice) "
        "|| STR(?p1) = ENCODE_FOR_URI(STR(?p1_uri_slice)))}"
    )


def test_query_deduplicates_identical_generated_patterns() -> None:
    mappings = pd.DataFrame([_rule("p1", "p1")])
    insert_columns(mappings)
    rule = mappings.iloc[0]
    query = Query([QueryTriple(rule), QueryTriple(rule), SubjectTriple(rule)])

    generated = query.generate(mappings)

    assert generated == (
        "SELECT ?p1 WHERE {?p1_uri <http://example.com/p1> ?p1 .\n"
        "FILTER(REGEX(STR(?p1_uri), 'http://example.com/table/([^/]*)'))\n"
        "BIND(STRAFTER(STR(?p1_uri), 'http://example.com/table/') as ?p1_uri_slice)\n"
        "FILTER(!BOUND(?p1) || STR(?p1) = STR(?p1_uri_slice) "
        "|| ENCODE_FOR_URI(STR(?p1)) = STR(?p1_uri_slice) "
        "|| STR(?p1) = ENCODE_FOR_URI(STR(?p1_uri_slice)))}"
    )


def test_adjacent_subject_template_skips_filter_when_references_are_already_bound() -> (
    None
):
    mappings = pd.DataFrame(
        [
            _two_column_rule("{p1}{p2}", "p1"),
            _two_column_rule("{p1}{p2}", "p2"),
        ]
    )
    insert_columns(mappings)
    first_rule = mappings.iloc[0]
    second_rule = mappings.iloc[1]
    id_generator = IdGenerator()
    codex = Codex()

    assert (
        QueryTriple(first_rule).generate(id_generator, codex, mappings)
        == "?p1__p2 <http://example.com/p1> ?p1 ."
    )
    assert (
        QueryTriple(second_rule).generate(id_generator, codex, mappings)
        == "?p1__p2 <http://example.com/p1> ?p2 ."
    )
    assert SubjectTriple(first_rule).generate(id_generator, codex, mappings) is None


def test_blank_node_template_strips_internal_label_prefix_before_extraction() -> None:
    rule_data = _rule("p1", "p2")
    rule_data["subject_map_value"] = "{p1}"
    rule_data["subject_termtype"] = RML_BLANK_NODE
    mappings = pd.DataFrame([rule_data])
    insert_columns(mappings)
    rule = mappings.iloc[0]
    id_generator = IdGenerator()
    codex = Codex()

    assert (
        QueryTriple(rule).generate(id_generator, codex, mappings)
        == "?p1 <http://example.com/p1> ?p2 ."
    )
    assert SubjectTriple(rule).generate(id_generator, codex, mappings) == (
        "BIND(REPLACE(REPLACE(STR(?p1), '^urn:bnode:', ''), '^_:', '') "
        "AS ?p1__blank_node_label)\n"
        "BIND(STRAFTER(STR(?p1__blank_node_label), '') as ?p1_2)"
    )


def test_ambiguous_subject_only_columns_are_non_invertible() -> None:
    mappings = pd.DataFrame([_rule("p1", "p1"), _rule("p2", "p1")])
    insert_columns(mappings)

    with pytest.raises(NonInvertibleError) as exc_info:
        _check_for_ambiguous_subject_templates(mappings)

    assert str(exc_info.value) == (
        "Subject templates for table 'data' contain columns that are not "
        "observable outside indistinguishable subjects: p2"
    )


def test_redundant_subject_groups_use_single_query_group() -> None:
    mappings = pd.DataFrame(
        [
            _predicate_rule(subject_column, object_column)
            for subject_column in ["p1", "p2", "p3", "p4", "p5"]
            for object_column in ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8"]
        ]
    )
    insert_columns(mappings)

    query_source_rules = _select_query_source_rules(mappings)
    query = Query(
        [QueryTriple(rule) for _, rule in query_source_rules.iterrows()]
        + [
            SubjectTriple(subject_rules.iloc[0])
            for _, subject_rules in query_source_rules.groupby(
                "subject_map_value", dropna=False
            )
        ]
    )
    generated = query.generate(mappings)

    assert generated is not None
    assert len(query_source_rules) == 8
    assert query_source_rules["subject_map_value"].unique().tolist() == [
        "http://example.com/table/{p1}"
    ]
    assert (
        generated.count(" ."),
        generated.count("FILTER("),
        generated.count("BIND("),
    ) == (8, 2, 1)


def test_query_solutions_are_converted_in_exact_chunks() -> None:
    store = Store()
    predicate = NamedNode("http://example.com/value")
    for identifier in range(1, 4):
        store.add(
            Quad(
                NamedNode(f"http://example.com/{identifier}"),
                predicate,
                Literal(identifier),
            )
        )
    solutions = store.query(
        "SELECT ?subject ?value WHERE { "
        "?subject <http://example.com/value> ?value "
        "} ORDER BY ?subject"
    )
    assert isinstance(solutions, QuerySolutions)

    chunks = list(_solutions_to_dataframes(solutions, chunk_size=2))

    assert [chunk.to_dict(orient="records") for chunk in chunks] == [
        [
            {"subject": "http://example.com/1", "value": 1},
            {"subject": "http://example.com/2", "value": 2},
        ],
        [{"subject": "http://example.com/3", "value": 3}],
    ]


def test_empty_query_solutions_produce_one_empty_chunk() -> None:
    store = Store()
    solutions = store.query("SELECT ?subject WHERE { ?subject ?p ?o }")
    assert isinstance(solutions, QuerySolutions)

    chunks = list(_solutions_to_dataframes(solutions, chunk_size=2))

    assert len(chunks) == 1
    assert chunks[0].columns.tolist() == ["subject"]
    assert chunks[0].to_dict(orient="records") == []


def test_incompatible_rdf_conversion_propagates() -> None:
    with pytest.raises(ValueError):
        sparql_to_python_type(
            "not-an-integer",
            "http://www.w3.org/2001/XMLSchema#integer",
        )


def test_incompatible_schema_conversion_propagates() -> None:
    column = ColumnInfo("id", "INTEGER", int)

    with pytest.raises(ValueError):
        infer_type_from_value_with_schema("not-an-integer", column)


def _predicate_rule(subject_column: str, object_column: str) -> dict[str, object]:
    rule = _rule(subject_column, object_column)
    rule["predicate_map_value"] = f"http://example.com/{object_column}"
    return rule
