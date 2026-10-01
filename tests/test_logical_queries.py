# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import pandas as pd

from conformance.outcome import logical_queries
from conformance.souffle_artifacts import translator_column_name
from kgi.constants import (
    RML_BLANK_NODE,
    RML_CONSTANT,
    RML_IRI,
    RML_LANGUAGE_MAP,
    RML_LITERAL,
    RML_QUERY_SOURCE,
    RML_REFERENCE,
    RML_TABLE_NAME,
)
from kgi.core import TableAnalysis, _name_logical_queries
from kgi.logical_queries import query_text, resolve_reference, source_tables
from kgi.query import Query
from kgi.triples import QueryTriple, SubjectTriple
from kgi.utils import insert_columns

JOIN_QUERY = """
    SELECT "Student"."ID" as "ID", "Sport"."Description" as "Description"
    FROM "Student","Sport","Student_Sport"
    WHERE "Student"."ID" = "Student_Sport"."ID_Student";
"""


def _query_rule(sql: str, language: str | None = None) -> dict[str, object]:
    return {
        "source_type": "RDB",
        "logical_source_type": RML_QUERY_SOURCE,
        "logical_source_value": sql,
        "iterator": None,
        "subject_map_type": RML_REFERENCE,
        "subject_map_value": "StudentId",
        "subject_termtype": RML_BLANK_NODE,
        "predicate_map_type": RML_CONSTANT,
        "predicate_map_value": "http://example.com/name",
        "object_map_type": RML_REFERENCE,
        "object_map_value": "Name",
        "object_termtype": RML_LITERAL,
        "lang_datatype": RML_LANGUAGE_MAP if language else None,
        "lang_datatype_map_type": RML_CONSTANT if language else None,
        "lang_datatype_map_value": language,
        "object_join_conditions": None,
        "graph_map_type": None,
        "graph_map_value": None,
        "triples_map_id": "TriplesMap1",
    }


def test_query_text_drops_the_terminator_and_surrounding_whitespace() -> None:
    assert (
        query_text('\n  SELECT "ID" FROM "Student";\n  ')
        == 'SELECT "ID" FROM "Student"'
    )


def test_source_tables_lists_every_table_the_query_reads() -> None:
    assert source_tables(JOIN_QUERY) == {"Student", "Sport", "Student_Sport"}


def test_resolve_reference_prefers_the_label_as_written() -> None:
    labels = ("StudentId", "ID", "Name")
    assert resolve_reference(labels, "StudentId") == "StudentId"
    assert resolve_reference(labels, "studentid") == "StudentId"
    assert resolve_reference(labels, "Surname") is None


def test_translator_column_name_mirrors_the_datalog_generator() -> None:
    assert translator_column_name("StudentId") == "studentid"
    assert translator_column_name('COUNT("Sport")') == "count__sport__"
    assert translator_column_name("count") == "column_count"
    assert translator_column_name("1st") == "column_1st"


def test_logical_queries_are_named_by_their_sorted_text() -> None:
    mappings = pd.DataFrame(
        [
            _query_rule("SELECT 1 FROM t WHERE x = 'ES';"),
            _query_rule("SELECT 1 FROM t WHERE x = 'EN'"),
            _query_rule("  SELECT 1 FROM t WHERE x = 'ES'  "),
            {**_query_rule("t"), "logical_source_type": RML_TABLE_NAME},
        ]
    )

    queries = _name_logical_queries(mappings)

    assert queries == {
        "query_1": "SELECT 1 FROM t WHERE x = 'EN'",
        "query_2": "SELECT 1 FROM t WHERE x = 'ES'",
    }
    assert list(mappings["logical_source_value"]) == [
        "query_2",
        "query_1",
        "query_2",
        "t",
    ]


def test_logical_queries_of_an_analysis_keep_only_query_defined_tables() -> None:
    analysis = {
        "Student": TableAnalysis(frozenset({"ID"}), frozenset(), ()),
        "query_1": TableAnalysis(frozenset({"ID"}), frozenset(), (), "SELECT 1"),
    }
    assert logical_queries(analysis) == {"query_1": "SELECT 1"}


def test_reference_blank_node_subject_reads_the_column_from_the_label() -> None:
    mappings = pd.DataFrame([_query_rule("SELECT 1")])
    insert_columns(mappings)
    rule = mappings.iloc[0]

    generated = Query([QueryTriple(rule), SubjectTriple(rule)]).generate(mappings)

    assert generated == (
        "SELECT ?StudentId ?Name WHERE {"
        "?StudentId_subject <http://example.com/name> ?Name .\n"
        "BIND(REPLACE(REPLACE(STR(?StudentId_subject), '^urn:bnode:', ''), "
        "'^_:', '') AS ?StudentId)}"
    ) or generated == (
        "SELECT ?Name ?StudentId WHERE {"
        "?StudentId_subject <http://example.com/name> ?Name .\n"
        "BIND(REPLACE(REPLACE(STR(?StudentId_subject), '^urn:bnode:', ''), "
        "'^_:', '') AS ?StudentId)}"
    )


def test_reference_iri_subject_leaves_the_column_to_other_term_maps() -> None:
    rule_data = _query_rule("SELECT 1")
    rule_data["subject_termtype"] = RML_IRI
    mappings = pd.DataFrame([rule_data])
    insert_columns(mappings)
    rule = mappings.iloc[0]

    query = Query([QueryTriple(rule), SubjectTriple(rule)])

    assert query.generate(mappings) is not None
    assert "BIND" not in str(query.generated_query)


def test_language_tagged_object_is_filtered_by_its_language() -> None:
    mappings = pd.DataFrame([_query_rule("SELECT 1", language="en")])
    insert_columns(mappings)
    rule = mappings.iloc[0]

    pattern = Query([QueryTriple(rule)]).generate(mappings)

    assert pattern is not None
    assert 'FILTER(LCASE(LANG(?Name)) = "en")' in pattern
