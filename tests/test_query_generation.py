# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from pathlib import Path

import pandas as pd
import pytest
from pyoxigraph import Literal, NamedNode, Quad, QuerySolutions, Store

from kgi.constants import (
    RML_BLANK_NODE,
    RML_CONSTANT,
    RML_IRI,
    RML_LITERAL,
    RML_PARENT_TRIPLES_MAP,
    RML_REFERENCE,
    RML_TEMPLATE,
)
from kgi.core import _check_for_unrecoverable_tables, _unrecoverable_references
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
        _check_for_unrecoverable_tables(mappings, unrecoverable)

    assert str(exc_info.value) == (
        "No column of table 'data' can be recovered from the graph: p1"
    )


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
    column = ColumnInfo("id", "INTEGER", int)

    with pytest.raises(ValueError):
        infer_type_from_value_with_schema("not-an-integer", column)


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
        "data1": frozenset(),
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
        _check_for_unrecoverable_tables(mappings, unrecoverable)
    assert (
        str(error.value)
        == "No column of table 'data2' can be recovered from the graph: id"
    )


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
