# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from pyoxigraph import BlankNode, Literal, NamedNode, Quad, QuerySolutions, Store
from sqlalchemy import DATE, INTEGER, LargeBinary

from kgi.constants import (
    RML_BLANK_NODE,
    RML_CONSTANT,
    RML_IRI,
    RML_LITERAL,
    RML_PARENT_TRIPLES_MAP,
    RML_REFERENCE,
    RML_TEMPLATE,
    RR_LOGICAL_TABLE,
    RR_PARENT_TRIPLES_MAP,
    RR_SUBJECT_MAP,
)
from kgi.core import (
    _analyze_rules,
    _check_for_unrecoverable_tables,
    _has_unreferenced_triples_map_without_generated_triples,
    _unrecoverable_references,
)
from kgi.endpoints import LocalSparqlGraphStore
from kgi.exceptions import NonInvertibleError
from kgi.query import (
    Query,
    _select_query_source_rules,
    _solutions_to_dataframes,
    query_triples,
)
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


def test_indistinguishable_subject_templates_lose_their_exclusive_columns(
    tmp_path,
) -> None:
    mappings = pd.DataFrame([_rule(f"p{index}", "p1") for index in (1, 2, 3)])
    insert_columns(mappings)

    assert _unrecoverable_references(mappings) == {"data": frozenset({"p2", "p3"})}
    generated = _query_for(mappings)
    assert generated == (
        "SELECT DISTINCT ?p1 WHERE {?p1_uri <http://example.com/p1> ?p1 .\n"
        "FILTER(REGEX(STR(?p1_uri), 'http://example.com/table/([^/]*)'))\n"
        "BIND(STRAFTER(STR(?p1_uri), 'http://example.com/table/') as ?p1_uri_slice)\n"
        "FILTER(!BOUND(?p1) || STR(?p1) = STR(?p1_uri_slice) "
        "|| ENCODE_FOR_URI(STR(?p1)) = STR(?p1_uri_slice) "
        "|| STR(?p1) = ENCODE_FOR_URI(STR(?p1_uri_slice)))}"
    )

    rdf_file = tmp_path / "data.nq"
    rdf_file.write_text(
        "\n".join(
            f'<http://example.com/table/{value}> <http://example.com/p1> "{row[0]}" .'
            for row in (("a", "b", "c"), ("x", "y", "z"))
            for value in row
        )
        + "\n",
        encoding="utf-8",
    )

    reconstructed = _reconstruct(rdf_file, mappings)[["p1"]]
    assert sorted(reconstructed.values.tolist()) == [["a"], ["x"]]


def test_indistinguishable_object_maps_lose_their_exclusive_columns() -> None:
    mappings = pd.DataFrame([_rule("p1", "p2"), _rule("p1", "p3")])
    insert_columns(mappings)

    assert _unrecoverable_references(mappings) == {"data": frozenset({"p2", "p3"})}
    select_variables, body = _graph_query(mappings)

    assert select_variables == ["?p1"]
    assert body == (
        "?p1_uri <http://example.com/p1> ?p2_object .\n"
        "?p1_uri <http://example.com/p1> ?p3_object .\n"
        "FILTER(REGEX(STR(?p1_uri), 'http://example.com/table/([^/]*)'))\n"
        "BIND(STRAFTER(STR(?p1_uri), 'http://example.com/table/') as ?p1)}"
    )

    distinguished_by_predicate = pd.DataFrame(
        [_predicate_rule("p1", "p2"), _predicate_rule("p1", "p3")]
    )
    insert_columns(distinguished_by_predicate)

    assert _unrecoverable_references(distinguished_by_predicate) == {
        "data": frozenset()
    }


def test_indistinguishable_graph_templates_lose_their_exclusive_columns() -> None:
    mappings = pd.DataFrame(
        [
            _graph_rule("p1", f"http://example.org/graph{{{graph_column}}}")
            for graph_column in ["p1", "p2", "p3", "p4", "p5"]
        ]
    )
    insert_columns(mappings)

    assert _unrecoverable_references(mappings) == {
        "data": frozenset({"p2", "p3", "p4", "p5"})
    }
    select_variables, body = _graph_query(mappings)

    assert select_variables == ["?p1"]
    assert body == (
        "?p1_uri <http://example.com/p1> ?p1 .\n"
        "FILTER(REGEX(STR(?p1_uri), 'http://example.com/table/([^/]*)'))\n"
        "BIND(STRAFTER(STR(?p1_uri), 'http://example.com/table/') as ?p1_uri_slice)\n"
        "FILTER(!BOUND(?p1) || STR(?p1) = STR(?p1_uri_slice) "
        "|| ENCODE_FOR_URI(STR(?p1)) = STR(?p1_uri_slice) "
        "|| STR(?p1) = ENCODE_FOR_URI(STR(?p1_uri_slice)))}"
    )


def test_column_iri_term_map_loses_only_the_column_no_other_map_exposes() -> None:
    opaque_rule = _rule("p1", "p1")
    opaque_rule["subject_map_type"] = RML_REFERENCE
    opaque_rule["subject_map_value"] = "p2"
    mappings = pd.DataFrame([opaque_rule])
    insert_columns(mappings)

    assert _unrecoverable_references(mappings) == {"data": frozenset({"p2"})}
    select_variables, body = _graph_query(mappings)

    assert select_variables == ["?p1"]
    assert body == "?p2_subject <http://example.com/p1> ?p1 .}"


def test_table_without_recoverable_columns_is_non_invertible() -> None:
    opaque_rule = _rule("p1", "p1")
    opaque_rule["subject_map_type"] = RML_REFERENCE
    opaque_rule["subject_map_value"] = "p1"
    opaque_rule["object_map_type"] = RML_CONSTANT
    opaque_rule["object_map_value"] = "http://example.com/Person"
    opaque_rule["object_termtype"] = RML_IRI
    mappings = pd.DataFrame([opaque_rule])
    insert_columns(mappings)
    unrecoverable = _unrecoverable_references(mappings)

    assert unrecoverable == {"data": frozenset({"p1"})}
    with pytest.raises(NonInvertibleError) as exc_info:
        _check_for_unrecoverable_tables(_analyze_rules(mappings))

    assert str(exc_info.value) == (
        "No column of table 'data' can be recovered from the graph: p1"
    )


def test_adjacent_subject_template_without_other_evidence_is_non_invertible() -> None:
    rule = _two_column_rule("{p1}{p2}", "p1")
    rule["object_map_type"] = RML_CONSTANT
    rule["object_map_value"] = "http://example.com/Person"
    rule["object_termtype"] = RML_IRI
    mappings = pd.DataFrame([rule])
    insert_columns(mappings)

    analysis = _analyze_rules(mappings)

    assert analysis["data"].unrecoverable == frozenset({"p1", "p2"})
    with pytest.raises(NonInvertibleError) as error:
        _check_for_unrecoverable_tables(analysis)
    assert str(error.value) == (
        "No column of table 'data' can be recovered from the graph: p1, p2"
    )


def test_adjacent_subject_template_uses_the_column_exposed_by_its_object(
    tmp_path: Path,
) -> None:
    mappings = pd.DataFrame([_two_column_rule("http://example.com/{p1}{p2}", "p1")])
    insert_columns(mappings)

    assert _unrecoverable_references(mappings) == {"data": frozenset({"p2"})}

    rdf_file = tmp_path / "data.nq"
    rdf_file.write_text(
        '<http://example.com/BobSmith> <http://example.com/p1> "Bob" .\n',
        encoding="utf-8",
    )

    reconstructed = _reconstruct(rdf_file, mappings)[["p1"]]
    assert reconstructed.values.tolist() == [["Bob"]]


def test_adjacent_object_template_is_not_used_as_subject_evidence() -> None:
    target_rule = _two_column_rule("{p1}{p2}", "p3")
    target_rule["logical_source_value"] = "target"
    evidence_rule = _two_column_rule("{p1}{p2}", "p1")
    evidence_rule["logical_source_value"] = "evidence"
    evidence_rule["object_map_type"] = RML_TEMPLATE
    evidence_rule["object_map_value"] = "{p1}{p2}"
    evidence_rule["object_termtype"] = RML_LITERAL
    mappings = pd.DataFrame([target_rule, evidence_rule])
    insert_columns(mappings)

    analysis = _analyze_rules(mappings)
    triples = query_triples(
        mappings,
        mappings.loc[mappings["logical_source_value"] == "target"],
    )

    assert analysis["evidence"].unrecoverable == frozenset({"p1", "p2"})
    assert analysis["target"].unrecoverable == frozenset({"p1", "p2"})
    assert [
        (type(triple), triple.rule["logical_source_value"]) for triple in triples
    ] == [(QueryTriple, "target"), (SubjectTriple, "target")]


def test_adjacent_object_template_does_not_disambiguate_subject_maps() -> None:
    first_rule = _two_column_rule("http://example.com/{p1}", "p1")
    first_rule["object_map_type"] = RML_TEMPLATE
    first_rule["object_map_value"] = "{p1}{p2}"
    first_rule["object_termtype"] = RML_LITERAL
    second_rule = _two_column_rule("http://example.com/{p2}", "p1")
    second_rule["triples_map_id"] = "TriplesMap_p2"
    second_rule["object_map_type"] = RML_TEMPLATE
    second_rule["object_map_value"] = "{p1}{p2}"
    second_rule["object_termtype"] = RML_LITERAL
    mappings = pd.DataFrame([first_rule, second_rule])
    insert_columns(mappings)

    assert _unrecoverable_references(mappings) == {"data": frozenset({"p1", "p2"})}


def test_adjacent_object_template_uses_separable_cross_table_evidence(
    tmp_path: Path,
) -> None:
    subject_template = "http://example.com/{p1}{p2}"
    target_rule = _two_column_rule(subject_template, "p1")
    target_rule["logical_source_value"] = "target"
    target_rule["predicate_map_value"] = "http://example.com/unsafe"
    target_rule["object_map_type"] = RML_TEMPLATE
    target_rule["object_map_value"] = "{p1}{p2}"
    target_rule["object_termtype"] = RML_LITERAL
    evidence_rule = _two_column_rule(subject_template, "p1")
    evidence_rule["logical_source_value"] = "evidence"
    evidence_rule["predicate_map_value"] = "http://example.com/safe"
    evidence_rule["object_map_type"] = RML_TEMPLATE
    evidence_rule["object_map_value"] = "{p1} {p2}"
    evidence_rule["object_termtype"] = RML_LITERAL
    mappings = pd.DataFrame([target_rule, evidence_rule])
    insert_columns(mappings)

    assert _unrecoverable_references(mappings) == {
        "evidence": frozenset(),
        "target": frozenset(),
    }

    rdf_file = tmp_path / "data.nq"
    rdf_file.write_text(
        '<http://example.com/BobSmith> <http://example.com/unsafe> "BobSmith" .\n'
        '<http://example.com/BobSmith> <http://example.com/safe> "Bob Smith" .\n',
        encoding="utf-8",
    )

    reconstructed = _reconstruct(rdf_file, mappings, "target")[["p1", "p2"]]
    assert reconstructed.values.tolist() == [["Bob", "Smith"]]


def test_local_object_maps_avoid_unneeded_cross_table_evidence(
    tmp_path: Path,
) -> None:
    subject_template = "http://example.com/{p1}{p2}"
    first_target_rule = _two_column_rule(subject_template, "p1")
    first_target_rule["logical_source_value"] = "target"
    second_target_rule = _two_column_rule(subject_template, "p2")
    second_target_rule["logical_source_value"] = "target"
    second_target_rule["predicate_map_value"] = "http://example.com/p2"
    evidence_rule = _two_column_rule(subject_template, "p1")
    evidence_rule["logical_source_value"] = "evidence"
    evidence_rule["predicate_map_value"] = "http://example.com/external"
    mappings = pd.DataFrame([first_target_rule, second_target_rule, evidence_rule])
    insert_columns(mappings)

    rdf_file = tmp_path / "data.nq"
    rdf_file.write_text(
        '<http://example.com/BobSmith> <http://example.com/p1> "Bob" .\n'
        '<http://example.com/BobSmith> <http://example.com/p2> "Smith" .\n',
        encoding="utf-8",
    )

    reconstructed = _reconstruct(rdf_file, mappings, "target")[["p1", "p2"]]
    assert reconstructed.values.tolist() == [["Bob", "Smith"]]


def test_adjacent_graph_template_columns_are_unrecoverable() -> None:
    mappings = pd.DataFrame([_graph_rule("p1", "http://example.org/{p2}{p3}")])
    insert_columns(mappings)

    assert _unrecoverable_references(mappings) == {"data": frozenset({"p2", "p3"})}
    select_variables, body = _graph_query(mappings)

    assert select_variables == ["?p1"]
    assert body == (
        "?p1_uri <http://example.com/p1> ?p1 .\n"
        "FILTER(REGEX(STR(?p1_uri), 'http://example.com/table/([^/]*)'))\n"
        "BIND(STRAFTER(STR(?p1_uri), 'http://example.com/table/') as ?p1_uri_slice)\n"
        "FILTER(!BOUND(?p1) || STR(?p1) = STR(?p1_uri_slice) "
        "|| ENCODE_FOR_URI(STR(?p1)) = STR(?p1_uri_slice) "
        "|| STR(?p1) = ENCODE_FOR_URI(STR(?p1_uri_slice)))}"
    )


def test_adjacent_object_template_does_not_disambiguate_graph_maps() -> None:
    first_rule = _graph_rule("p1", "http://example.org/{p2}")
    first_rule["object_map_type"] = RML_TEMPLATE
    first_rule["object_map_value"] = "{p2}{p3}"
    first_rule["object_termtype"] = RML_LITERAL
    second_rule = _graph_rule("p1", "http://example.org/{p3}")
    second_rule["object_map_type"] = RML_TEMPLATE
    second_rule["object_map_value"] = "{p2}{p3}"
    second_rule["object_termtype"] = RML_LITERAL
    mappings = pd.DataFrame([first_rule, second_rule])
    insert_columns(mappings)

    assert _unrecoverable_references(mappings) == {"data": frozenset({"p2", "p3"})}


def test_graph_columns_exposed_by_object_maps_are_read_from_the_default_graph() -> None:
    mappings = pd.DataFrame(
        [
            _graph_rule(object_column, f"http://example.org/graph{{{graph_column}}}")
            for object_column in ["p1", "p2", "p3"]
            for graph_column in ["p1", "p2", "p3"]
        ]
    )
    insert_columns(mappings)

    select_variables, body = _graph_query(mappings)

    assert select_variables == ["?p1", "?p2", "?p3"]
    assert body == (
        "?p1_uri <http://example.com/p1> ?p1 .\n"
        "?p1_uri <http://example.com/p2> ?p2 .\n"
        "?p1_uri <http://example.com/p3> ?p3 .\n"
        "FILTER(REGEX(STR(?p1_uri), 'http://example.com/table/([^/]*)'))\n"
        "BIND(STRAFTER(STR(?p1_uri), 'http://example.com/table/') as ?p1_uri_slice)\n"
        "FILTER(!BOUND(?p1) || STR(?p1) = STR(?p1_uri_slice) "
        "|| ENCODE_FOR_URI(STR(?p1)) = STR(?p1_uri_slice) "
        "|| STR(?p1) = ENCODE_FOR_URI(STR(?p1_uri_slice)))}"
    )


def test_graph_template_column_is_extracted_from_the_named_graph() -> None:
    mappings = pd.DataFrame([_graph_rule("p1", "http://example.org/graph{p2}")])
    insert_columns(mappings)

    select_variables, body = _graph_query(mappings)

    assert select_variables == ["?p1", "?p2"]
    assert body == (
        "?p1_uri <http://example.com/p1> ?p1 .\n"
        "FILTER(REGEX(STR(?p1_uri), 'http://example.com/table/([^/]*)'))\n"
        "BIND(STRAFTER(STR(?p1_uri), 'http://example.com/table/') as ?p1_uri_slice)\n"
        "FILTER(!BOUND(?p1) || STR(?p1) = STR(?p1_uri_slice) "
        "|| ENCODE_FOR_URI(STR(?p1)) = STR(?p1_uri_slice) "
        "|| STR(?p1) = ENCODE_FOR_URI(STR(?p1_uri_slice)))\n"
        "GRAPH ?graph_p2_uri {\n"
        "?p1_uri <http://example.com/p1> ?p1 .\n"
        "}\n"
        "FILTER(REGEX(STR(?graph_p2_uri), 'http://example.org/graph([^/]*)'))\n"
        "BIND(STRAFTER(STR(?graph_p2_uri), 'http://example.org/graph') as ?p2)}"
    )


def test_reference_graph_map_binds_the_graph_iri_to_its_own_variable() -> None:
    rule = _graph_rule("p1", "p2")
    rule["graph_map_type"] = RML_REFERENCE
    mappings = pd.DataFrame([rule])
    insert_columns(mappings)

    select_variables, body = _graph_query(mappings)

    assert select_variables == ["?p1", "?p2"]
    assert body == (
        "?p1_uri <http://example.com/p1> ?p1 .\n"
        "FILTER(REGEX(STR(?p1_uri), 'http://example.com/table/([^/]*)'))\n"
        "BIND(STRAFTER(STR(?p1_uri), 'http://example.com/table/') as ?p1_uri_slice)\n"
        "FILTER(!BOUND(?p1) || STR(?p1) = STR(?p1_uri_slice) "
        "|| ENCODE_FOR_URI(STR(?p1)) = STR(?p1_uri_slice) "
        "|| STR(?p1) = ENCODE_FOR_URI(STR(?p1_uri_slice)))\n"
        "GRAPH ?p2_graph {\n"
        "?p1_uri <http://example.com/p1> ?p1 .\n"
        "}\n"
        "BIND(STR(?p2_graph) AS ?p2)}"
    )


def test_several_graph_maps_are_inverted_on_separate_graph_variables(
    tmp_path,
) -> None:
    mappings = pd.DataFrame(
        [
            _graph_rule("p1", "http://example.org/a/{p2}"),
            _graph_rule("p1", "http://example.org/b/{p3}"),
        ]
    )
    insert_columns(mappings)

    rdf_file = tmp_path / "data.nq"
    rdf_file.write_text(
        "\n".join(
            f'<http://example.com/table/{p1}> <http://example.com/p1> "{p1}" '
            f"<http://example.org/{prefix}/{value}> ."
            for p1, p2, p3 in (("a", "b", "c"), ("x", "y", "z"))
            for prefix, value in (("a", p2), ("b", p3))
        )
        + "\n",
        encoding="utf-8",
    )

    reconstructed = _reconstruct(rdf_file, mappings)[["p1", "p2", "p3"]]
    assert sorted(reconstructed.values.tolist()) == [
        ["a", "b", "c"],
        ["x", "y", "z"],
    ]


def test_graph_maps_sharing_a_pattern_lose_their_exclusive_column() -> None:
    mappings = pd.DataFrame(
        [
            _graph_rule("p1", "http://example.org/graph{p1}"),
            _graph_rule("p1", "http://example.org/graph{p2}"),
        ]
    )
    insert_columns(mappings)

    assert _unrecoverable_references(mappings) == {"data": frozenset({"p2"})}
    select_variables, body = _graph_query(mappings)

    assert select_variables == ["?p1"]
    assert body == (
        "?p1_uri <http://example.com/p1> ?p1 .\n"
        "FILTER(REGEX(STR(?p1_uri), 'http://example.com/table/([^/]*)'))\n"
        "BIND(STRAFTER(STR(?p1_uri), 'http://example.com/table/') as ?p1_uri_slice)\n"
        "FILTER(!BOUND(?p1) || STR(?p1) = STR(?p1_uri_slice) "
        "|| ENCODE_FOR_URI(STR(?p1)) = STR(?p1_uri_slice) "
        "|| STR(?p1) = ENCODE_FOR_URI(STR(?p1_uri_slice)))}"
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
    column = ColumnInfo("id", INTEGER(), int)

    with pytest.raises(ValueError):
        infer_type_from_value_with_schema("not-an-integer", column)


def test_schema_conversion_keeps_dates_and_decodes_binary() -> None:
    birth_date = ColumnInfo("birth_date", DATE(), date)
    photo = ColumnInfo("photo", LargeBinary(), bytes)

    assert infer_type_from_value_with_schema(date(1981, 10, 10), birth_date) == date(
        1981, 10, 10
    )
    assert infer_type_from_value_with_schema("89504E47", photo) == b"\x89PNG"


def _join_mappings() -> pd.DataFrame:
    """A join whose parent triples map has no predicate-object map, as KROWN builds."""
    child = _rule("id", "id")
    child["logical_source_value"] = "data1"
    child["subject_map_value"] = "http://example.com/table1/{id}"
    child["triples_map_id"] = "http://example.com/#TriplesMap1"
    child["predicate_map_value"] = "http://example.com/j1"
    child["object_map_type"] = RML_PARENT_TRIPLES_MAP
    child["object_map_value"] = "http://example.com/#TriplesMap2"
    child["object_termtype"] = RML_IRI
    child["object_join_conditions"] = (
        "{'0': {'child_value': 'p1', 'parent_value': 'p1'}}"
    )

    parent = _rule("id", "id")
    parent["logical_source_value"] = "data2"
    parent["subject_map_value"] = "http://example.com/table2/{id}"
    parent["triples_map_id"] = "http://example.com/#TriplesMap2"
    parent["predicate_map_type"] = None
    parent["predicate_map_value"] = None
    parent["object_map_type"] = None
    parent["object_map_value"] = None
    parent["object_termtype"] = None
    return pd.DataFrame([child, parent])


def test_parent_triples_map_without_predicate_object_map_is_read_from_the_join(
    tmp_path,
) -> None:
    mappings = _join_mappings()
    insert_columns(mappings)

    assert _unrecoverable_references(mappings) == {
        "data1": frozenset({"p1"}),
        "data2": frozenset(),
    }
    assert _build_query(mappings, "data2")[1] == (
        "SELECT ?id WHERE {?TriplesMap2_referencing_subject "
        "<http://example.com/j1> ?id_uri .\n"
        "FILTER(REGEX(STR(?id_uri), 'http://example.com/table2/([^/]*)'))\n"
        "BIND(STRAFTER(STR(?id_uri), 'http://example.com/table2/') as ?id)}"
    )

    rdf_file = tmp_path / "data.nq"
    rdf_file.write_text(
        "\n".join(
            f"<http://example.com/table1/{child}> <http://example.com/j1> "
            f"<http://example.com/table2/{parent}> ."
            for child, parent in (("1", "10"), ("2", "20"))
        )
        + "\n",
        encoding="utf-8",
    )

    reconstructed = _reconstruct(rdf_file, mappings, "data2")
    assert sorted(reconstructed[["id"]].values.tolist()) == [["10"], ["20"]]

    reconstructed = _reconstruct(rdf_file, mappings, "data1")
    assert sorted(reconstructed[["id"]].values.tolist()) == [["1"], ["2"]]


def test_join_key_exposed_by_another_object_map_is_reconstructed(tmp_path) -> None:
    mappings = _join_mappings()
    exposed_join_key = mappings.iloc[0].copy()
    exposed_join_key["predicate_map_value"] = "http://example.com/joinKey"
    exposed_join_key["object_map_type"] = RML_REFERENCE
    exposed_join_key["object_map_value"] = "p1"
    exposed_join_key["object_termtype"] = RML_LITERAL
    exposed_join_key["object_join_conditions"] = None
    mappings = pd.concat([mappings, exposed_join_key.to_frame().T], ignore_index=True)
    insert_columns(mappings)

    rdf_file = tmp_path / "data.nq"
    rdf_file.write_text(
        "<http://example.com/table1/1> <http://example.com/j1> "
        "<http://example.com/table2/10> .\n"
        '<http://example.com/table1/1> <http://example.com/joinKey> "a" .\n',
        encoding="utf-8",
    )

    reconstructed = _reconstruct(rdf_file, mappings, "data1")
    assert reconstructed.to_dict(orient="records") == [{"id": "1", "p1": "a"}]


def test_triples_map_without_predicate_object_map_needs_a_join_to_be_invertible() -> (
    None
):
    mappings = _join_mappings()
    mappings = mappings.loc[mappings["logical_source_value"] == "data2"].reset_index(
        drop=True
    )
    insert_columns(mappings)

    unrecoverable = _unrecoverable_references(mappings)
    assert unrecoverable == {"data2": frozenset({"id"})}
    with pytest.raises(NonInvertibleError) as error:
        _check_for_unrecoverable_tables(_analyze_rules(mappings))
    assert (
        str(error.value)
        == "No column of table 'data2' can be recovered from the graph: id"
    )


def test_subject_class_generates_triples_without_a_predicate_object_map() -> None:
    store = Store()
    triples_map = NamedNode("http://example.com/TriplesMap")
    subject_map = BlankNode()
    store.add(Quad(triples_map, RR_LOGICAL_TABLE, BlankNode()))
    store.add(Quad(triples_map, RR_SUBJECT_MAP, subject_map))
    store.add(
        Quad(
            subject_map,
            NamedNode("http://www.w3.org/ns/r2rml#class"),
            NamedNode("http://example.com/Person"),
        )
    )

    assert not _has_unreferenced_triples_map_without_generated_triples(store)


def test_incoming_join_carries_a_triples_map_without_a_predicate_object_map() -> None:
    store = Store()
    triples_map = NamedNode("http://example.com/TriplesMap")
    store.add(Quad(triples_map, RR_LOGICAL_TABLE, BlankNode()))
    store.add(Quad(BlankNode(), RR_PARENT_TRIPLES_MAP, triples_map))

    assert not _has_unreferenced_triples_map_without_generated_triples(store)


def _predicate_rule(subject_column: str, object_column: str) -> dict[str, object]:
    rule = _rule(subject_column, object_column)
    rule["predicate_map_value"] = f"http://example.com/{object_column}"
    return rule


def _graph_rule(object_column: str, graph_template: str) -> dict[str, object]:
    rule = _predicate_rule("p1", object_column)
    rule["graph_map_type"] = RML_TEMPLATE
    rule["graph_map_value"] = graph_template
    return rule


def _build_query(mappings: pd.DataFrame, table: str = "data") -> tuple[Query, str]:
    """Build the inversion query the way `retrieve_data` does."""
    excluded = _unrecoverable_references(mappings)[table]
    source_rules = mappings.loc[mappings["logical_source_value"] == table]
    query_source_rules = _select_query_source_rules(source_rules, excluded)
    query = Query(query_triples(mappings, query_source_rules, excluded), excluded)
    generated = query.generate(mappings)
    assert generated is not None
    return query, generated


def _query_for(mappings: pd.DataFrame) -> str:
    return _build_query(mappings)[1]


def _reconstruct(
    rdf_file: Path, mappings: pd.DataFrame, table: str = "data"
) -> pd.DataFrame:
    """Run the generated query against a store holding the given RDF."""
    query, generated = _build_query(mappings, table)
    endpoint = LocalSparqlGraphStore(str(rdf_file))
    try:
        chunks = [
            query.decode_dataframe(chunk)
            for chunk in _solutions_to_dataframes(endpoint.query(generated))
        ]
    finally:
        endpoint.close()
    return pd.concat(chunks)


def _graph_query(mappings: pd.DataFrame) -> tuple[list[str], str]:
    """Generate the query and split its unordered SELECT list from its body."""
    select_part, _, body = _query_for(mappings).partition(" WHERE {")
    variables = select_part.removeprefix("SELECT").removeprefix(" DISTINCT").split()
    return sorted(variables), body
