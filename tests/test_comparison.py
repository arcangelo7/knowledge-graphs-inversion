# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from kgi.comparison import DatabaseContent, PartialLoss, compare_databases
from kgi.core import MappingAnalysis, TableAnalysis

PEOPLE = TableAnalysis(
    references=frozenset({"fname", "lname"}),
    unrecoverable=frozenset(),
    subject_reference_sets=(frozenset({"fname", "lname"}),),
)


def _content(rows: list[list[object]], columns: list[str]) -> DatabaseContent:
    return {"people": {"columns": columns, "data": rows}}


def _analysis(table: TableAnalysis) -> MappingAnalysis:
    return {"people": table}


def test_wrong_values_in_recovered_columns_are_not_excused_by_a_lost_column() -> None:
    source = _content([["Bob", "Smith", "30"]], ["fname", "lname", "amount"])
    destination = _content([["Bob", "WRONG"]], ["fname", "lname"])

    equal, message, losses = compare_databases(source, destination, _analysis(PEOPLE))

    assert equal is False
    assert losses == frozenset()
    assert message == (
        "Unexplained differences: people (data differs from the expected projection)"
    )


def test_only_the_unrecoverable_column_is_reported_as_lost() -> None:
    source = _content([["Bob", "Smith", "30"]], ["fname", "lname", "amount"])
    destination = _content([["Bob", "Smith"]], ["fname", "lname"])

    equal, message, losses = compare_databases(source, destination, _analysis(PEOPLE))

    assert equal is False
    assert losses == frozenset({PartialLoss.COLUMNS_LOST})
    assert message == "Characterised loss - people: columns not recovered: amount"


def test_collapsed_duplicates_are_reported_wherever_they_sort() -> None:
    columns = ["fname", "lname"]
    destination = _content([["Ann", "Jones"], ["Bob", "Smith"]], columns)
    duplicate_sorts_first = _content(
        [["Ann", "Jones"], ["Ann", "Jones"], ["Bob", "Smith"]], columns
    )
    duplicate_sorts_last = _content(
        [["Ann", "Jones"], ["Bob", "Smith"], ["Bob", "Smith"]], columns
    )

    for source in (duplicate_sorts_first, duplicate_sorts_last):
        equal, message, losses = compare_databases(
            source, destination, _analysis(PEOPLE)
        )

        assert equal is False
        assert losses == frozenset({PartialLoss.MULTIPLICITY_LOST})
        assert message == "Characterised loss - people: 1 duplicate row(s) collapsed"


def test_rows_dropped_for_a_null_subject_column_are_reported_as_rows_lost() -> None:
    columns = ["fname", "lname"]
    source = _content([["Ann", None], ["Bob", "Smith"]], columns)
    destination = _content([["Bob", "Smith"]], columns)

    equal, message, losses = compare_databases(source, destination, _analysis(PEOPLE))

    assert equal is False
    assert losses == frozenset({PartialLoss.ROWS_LOST})
    assert message == (
        "Characterised loss - people: 1 row(s) with NULL in a subject template column"
    )


def test_an_unmapped_table_is_reported_as_lost_without_touching_the_others() -> None:
    source: DatabaseContent = {
        "people": {"columns": ["fname", "lname"], "data": [["Bob", "Smith"]]},
        "cities": {"columns": ["name"], "data": [["London"]]},
    }
    destination: DatabaseContent = {
        "people": {"columns": ["fname", "lname"], "data": [["Bob", "Smith"]]},
    }

    equal, message, losses = compare_databases(source, destination, _analysis(PEOPLE))

    assert equal is False
    assert losses == frozenset({PartialLoss.TABLES_LOST})
    assert message == "Characterised loss - cities: unmapped table"


def test_an_exact_reconstruction_reports_no_loss() -> None:
    columns = ["fname", "lname"]
    content = _content([["Bob", "Smith"]], columns)

    equal, message, losses = compare_databases(content, content, _analysis(PEOPLE))

    assert equal is True
    assert losses == frozenset()
    assert message == "All tables in source and destination databases are identical"
