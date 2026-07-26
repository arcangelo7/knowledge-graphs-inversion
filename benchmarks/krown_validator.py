# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import filecmp
import os
import re
import subprocess
from pathlib import Path

from pyoxigraph import BlankNode, Literal, NamedNode, RdfFormat, Store
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from kgi.constants import (
    REF_TEMPLATE_REGEX,
    RML_CHILD,
    RML_ITERATOR,
    RML_LOGICAL_SOURCE,
    RML_OLD_LOGICAL_SOURCE,
    RML_OLD_REFERENCE,
    RML_PARENT_TRIPLES_MAP,
    RML_REFERENCE_NODE,
    RML_TEMPLATE_NODE,
    RR_CHILD,
    RR_COLUMN,
    RR_LOGICAL_TABLE,
    RR_PARENT_TRIPLES_MAP,
    RR_TABLE_NAME,
    RR_TEMPLATE,
)
from kgi.utils import normalize_sql_identifier

LOGICAL_SOURCE_PREDICATES = (
    RR_LOGICAL_TABLE,
    RML_LOGICAL_SOURCE,
    RML_OLD_LOGICAL_SOURCE,
)
TABLE_NAME_PREDICATES = (RR_TABLE_NAME, RML_ITERATOR)
TEMPLATE_PREDICATES = (RR_TEMPLATE, RML_TEMPLATE_NODE)
COLUMN_PREDICATES = (
    RR_COLUMN,
    RML_REFERENCE_NODE,
    RML_OLD_REFERENCE,
    RR_CHILD,
    RML_CHILD,
)
# A referencing object map reads columns of the parent triples map's own table
PARENT_TRIPLES_MAP_PREDICATES = (
    RR_PARENT_TRIPLES_MAP,
    NamedNode(RML_PARENT_TRIPLES_MAP),
)


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table_name(store: Store, logical_source: NamedNode | BlankNode) -> str | None:
    for predicate in TABLE_NAME_PREDICATES:
        for quad in store.quads_for_pattern(logical_source, predicate, None):
            if isinstance(quad.object, Literal):
                return normalize_sql_identifier(quad.object.value)
    return None


def _term_map_columns(store: Store, triples_map: NamedNode | BlankNode) -> set[str]:
    """Source columns the term maps of one triples map read."""
    columns: set[str] = set()
    visited: set[NamedNode | BlankNode] = {triples_map}
    pending = [triples_map]
    while pending:
        node = pending.pop()
        for quad in store.quads_for_pattern(node, None, None):
            if quad.predicate in PARENT_TRIPLES_MAP_PREDICATES:
                continue
            value = quad.object
            if isinstance(value, Literal):
                if quad.predicate in TEMPLATE_PREDICATES:
                    columns.update(
                        normalize_sql_identifier(reference)
                        for reference in re.findall(REF_TEMPLATE_REGEX, value.value)
                    )
                elif quad.predicate in COLUMN_PREDICATES:
                    columns.add(normalize_sql_identifier(value.value))
            elif isinstance(value, (NamedNode, BlankNode)) and value not in visited:
                visited.add(value)
                pending.append(value)
    return columns


def _mapped_columns(mapping_file: Path) -> dict[str, set[str]]:
    """Source columns the mapping reads, per logical table."""
    store = Store()
    store.load(path=str(mapping_file), format=RdfFormat.TURTLE)
    columns: dict[str, set[str]] = {}
    for predicate in LOGICAL_SOURCE_PREDICATES:
        for quad in store.quads_for_pattern(None, predicate, None):
            if not isinstance(quad.object, (NamedNode, BlankNode)) or not isinstance(
                quad.subject, (NamedNode, BlankNode)
            ):
                continue
            table_name = _table_name(store, quad.object)
            if table_name is None:
                continue
            columns.setdefault(table_name, set()).update(
                _term_map_columns(store, quad.subject)
            )
    return columns


class KrownValidator:
    def __init__(
        self,
        connection_string: str,
        source_schema: str,
        destination_schema: str,
    ):
        self.engine: Engine = create_engine(connection_string)
        self.source_schema = source_schema
        self.destination_schema = destination_schema

    @staticmethod
    def _qualified(schema: str, table_name: str) -> str:
        return f"{_quoted(schema)}.{_quoted(table_name)}"

    def _columns(self, schema: str, table_name: str) -> list[str]:
        return [
            column["name"]
            for column in inspect(self.engine).get_columns(table_name, schema=schema)
        ]

    def _populated_columns(self, schema: str, table_name: str) -> list[str]:
        """Columns the reconstruction filled with at least one value.

        An engine that recreates the whole source schema pads unrecovered cells with
        NULL. Such a column carries nothing the graph provided, so it counts as not
        reconstructed, exactly like a column the engine leaves out.
        """
        columns = self._columns(schema, table_name)
        counts = ", ".join(f"COUNT({_quoted(column)})" for column in columns)
        query = text(f"SELECT {counts} FROM {self._qualified(schema, table_name)}")
        with self.engine.connect() as connection:
            populated = connection.execute(query).one()
        return [column for column, count in zip(columns, populated) if count]

    def _row_count(self, schema: str, table_name: str) -> int:
        with self.engine.connect() as connection:
            return int(
                connection.execute(
                    text(f"SELECT COUNT(*) FROM {self._qualified(schema, table_name)}")
                ).scalar_one()
            )

    def _difference_exists(
        self,
        left_schema: str,
        left_table: str,
        right_schema: str,
        right_table: str,
        columns: list[str],
        preserve_multiplicity: bool,
    ) -> bool:
        column_list = ", ".join(_quoted(column) for column in columns)
        operator = "EXCEPT ALL" if preserve_multiplicity else "EXCEPT"
        query = text(
            "SELECT EXISTS ("
            "SELECT 1 FROM ("
            f"SELECT {column_list} FROM "
            f"{self._qualified(left_schema, left_table)} "
            f"{operator} "
            f"SELECT {column_list} FROM "
            f"{self._qualified(right_schema, right_table)}"
            ") AS difference)"
        )
        with self.engine.connect() as connection:
            return bool(connection.execute(query).scalar_one())

    def _table_result(self, table_name: str) -> dict[str, object]:
        original_columns = self._columns(self.source_schema, table_name)
        reconstructed_columns = self._populated_columns(
            self.destination_schema, table_name
        )
        original_rows = self._row_count(self.source_schema, table_name)
        reconstructed_rows = self._row_count(self.destination_schema, table_name)
        extra_columns = [
            column for column in reconstructed_columns if column not in original_columns
        ]
        lost_columns = [
            column for column in original_columns if column not in reconstructed_columns
        ]
        ordered_projection = [
            column for column in original_columns if column in reconstructed_columns
        ]
        column_subset = (
            bool(reconstructed_columns)
            and not extra_columns
            and reconstructed_columns == ordered_projection
        )

        if column_subset:
            foreign_values = self._difference_exists(
                self.destination_schema,
                table_name,
                self.source_schema,
                table_name,
                reconstructed_columns,
                preserve_multiplicity=False,
            )
            missing_values = self._difference_exists(
                self.source_schema,
                table_name,
                self.destination_schema,
                table_name,
                reconstructed_columns,
                preserve_multiplicity=False,
            )
            multiplicities_match = not self._difference_exists(
                self.destination_schema,
                table_name,
                self.source_schema,
                table_name,
                reconstructed_columns,
                preserve_multiplicity=True,
            ) and not self._difference_exists(
                self.source_schema,
                table_name,
                self.destination_schema,
                table_name,
                reconstructed_columns,
                preserve_multiplicity=True,
            )
        else:
            foreign_values = True
            missing_values = True
            multiplicities_match = False

        values_match = not foreign_values and not missing_values
        rows_match = original_rows == reconstructed_rows
        exact = (
            original_columns == reconstructed_columns
            and rows_match
            and values_match
            and multiplicities_match
        )
        return {
            "checks": {
                "column_subset": column_subset,
                "no_foreign_values": not foreign_values,
                "columns": original_columns == reconstructed_columns,
                "rows": rows_match,
                "values": values_match,
                "multiplicities": multiplicities_match,
            },
            "losses": {
                "columns": lost_columns,
                "rows": max(original_rows - reconstructed_rows, 0),
                "additional_rows": max(reconstructed_rows - original_rows, 0),
                "values": missing_values,
                "multiplicities": not multiplicities_match,
            },
            "metrics": {
                "original_rows": original_rows,
                "reconstructed_rows": reconstructed_rows,
                "original_columns": original_columns,
                "reconstructed_columns": reconstructed_columns,
            },
            "exact": exact,
            "partial_valid": column_subset and not foreign_values,
        }

    @staticmethod
    def _sort_rdf_dataset(rdf_file: Path, output: Path) -> None:
        environment = {**os.environ, "LC_ALL": "C"}
        subprocess.run(
            ["sort", "-u", str(rdf_file), "-o", str(output)],
            check=True,
            env=environment,
        )

    @classmethod
    def _rdf_datasets_equal(cls, original: Path, roundtrip: Path) -> bool:
        directory = original.parent
        original_sorted = directory / ".krown-original-sorted.nq"
        roundtrip_sorted = directory / ".krown-roundtrip-sorted.nq"
        try:
            cls._sort_rdf_dataset(original, original_sorted)
            cls._sort_rdf_dataset(roundtrip, roundtrip_sorted)
            return filecmp.cmp(original_sorted, roundtrip_sorted, shallow=False)
        finally:
            original_sorted.unlink(missing_ok=True)
            roundtrip_sorted.unlink(missing_ok=True)

    def missing_mapped_columns(
        self, expected_tables: list[str], mapping_file: Path
    ) -> dict[str, list[str]]:
        """Columns the mapping reads that the reconstruction does not provide.

        The mapping cannot rebuild the graph from tables that lack them, so the round
        trip is not attempted and the reconstruction is reported as `AMBIGUOUS`.
        """
        columns = _mapped_columns(mapping_file)
        destination_tables = set(
            inspect(self.engine).get_table_names(schema=self.destination_schema)
        )
        missing: dict[str, list[str]] = {}
        for table_name in expected_tables:
            if table_name not in columns:
                continue
            reconstructed = (
                set(self._populated_columns(self.destination_schema, table_name))
                if table_name in destination_tables
                else set()
            )
            absent = sorted(columns[table_name] - reconstructed)
            if absent:
                missing[table_name] = absent
        return missing

    def validate_inversion(
        self,
        expected_tables: list[str],
        scenario_name: str,
        expected_outcome: str,
        original_rdf: Path,
        roundtrip_rdf: Path | None,
        missing_mapped_columns: dict[str, list[str]],
    ) -> dict[str, object]:
        inspector = inspect(self.engine)
        source_tables = set(inspector.get_table_names(schema=self.source_schema))
        destination_tables = set(
            inspector.get_table_names(schema=self.destination_schema)
        )
        expected_table_names = set(expected_tables)
        table_names_match = (
            source_tables == expected_table_names
            and destination_tables == expected_table_names
        )
        table_results = {}
        if table_names_match:
            for table_name in expected_tables:
                table_results[table_name] = self._table_result(table_name)

        if missing_mapped_columns:
            rdf_round_trip = None
        else:
            assert roundtrip_rdf is not None
            rdf_round_trip = self._rdf_datasets_equal(original_rdf, roundtrip_rdf)

        sound = table_names_match and all(
            bool(result["partial_valid"]) for result in table_results.values()
        )
        exact = (
            sound
            and rdf_round_trip is True
            and all(bool(result["exact"]) for result in table_results.values())
        )
        if exact:
            outcome = "FULL"
        elif sound and rdf_round_trip is True:
            outcome = "PARTIAL"
        elif sound and rdf_round_trip is None:
            outcome = "AMBIGUOUS"
        else:
            outcome = "MISMATCH"

        errors = []
        if not table_names_match:
            errors.append("tables")
        if rdf_round_trip is False:
            errors.append("rdf_round_trip")
        for table_name, result in table_results.items():
            checks = result["checks"]
            if checks["column_subset"] is not True:
                errors.append(f"{table_name}.columns")
            if checks["no_foreign_values"] is not True:
                errors.append(f"{table_name}.values")

        outcome_matches_expectation = outcome == expected_outcome
        if not outcome_matches_expectation:
            errors.append("outcome")
        return {
            "scenario": scenario_name,
            "validation_passed": outcome_matches_expectation,
            "expected_outcome": expected_outcome,
            "outcome": outcome,
            "checks": {
                "tables": table_names_match,
                "rdf_round_trip": rdf_round_trip,
            },
            "missing_mapped_columns": missing_mapped_columns,
            "source_tables": sorted(source_tables),
            "destination_tables": sorted(destination_tables),
            "tables": table_results,
            "errors": errors,
        }

    def dispose(self) -> None:
        self.engine.dispose()
