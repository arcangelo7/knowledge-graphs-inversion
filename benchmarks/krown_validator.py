# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import filecmp
import os
import subprocess
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


class KrownValidator:
    def __init__(self, connection_string: str):
        self.engine: Engine = create_engine(connection_string)

    def _columns(self, table_name: str) -> list[str]:
        return [
            column["name"] for column in inspect(self.engine).get_columns(table_name)
        ]

    def _row_count(self, table_name: str) -> int:
        with self.engine.connect() as connection:
            return int(
                connection.execute(
                    text(f"SELECT COUNT(*) FROM {_quoted(table_name)}")
                ).scalar_one()
            )

    def _difference_exists(
        self,
        left_table: str,
        right_table: str,
        columns: list[str],
        preserve_multiplicity: bool,
    ) -> bool:
        column_list = ", ".join(_quoted(column) for column in columns)
        operator = "EXCEPT ALL" if preserve_multiplicity else "EXCEPT"
        query = text(
            "SELECT EXISTS ("
            "SELECT 1 FROM ("
            f"SELECT {column_list} FROM {_quoted(left_table)} "
            f"{operator} "
            f"SELECT {column_list} FROM {_quoted(right_table)}"
            ") AS difference)"
        )
        with self.engine.connect() as connection:
            return bool(connection.execute(query).scalar_one())

    def _table_result(
        self, original_table: str, reconstructed_table: str
    ) -> dict[str, object]:
        original_columns = self._columns(original_table)
        reconstructed_columns = self._columns(reconstructed_table)
        original_rows = self._row_count(original_table)
        reconstructed_rows = self._row_count(reconstructed_table)
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
                reconstructed_table,
                original_table,
                reconstructed_columns,
                preserve_multiplicity=False,
            )
            missing_values = self._difference_exists(
                original_table,
                reconstructed_table,
                reconstructed_columns,
                preserve_multiplicity=False,
            )
            multiplicities_match = not self._difference_exists(
                reconstructed_table,
                original_table,
                reconstructed_columns,
                preserve_multiplicity=True,
            ) and not self._difference_exists(
                original_table,
                reconstructed_table,
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

    def validate_inversion(
        self,
        original_tables: dict[str, str],
        reconstructed_tables: list[str],
        scenario_name: str,
        expected_outcome: str,
        original_rdf: Path,
        roundtrip_rdf: Path,
    ) -> dict[str, object]:
        table_names_match = set(original_tables) == set(reconstructed_tables)
        table_results = {}
        if table_names_match:
            for table_name in reconstructed_tables:
                table_results[table_name] = self._table_result(
                    original_tables[table_name], table_name
                )

        rdf_round_trip = self._rdf_datasets_equal(original_rdf, roundtrip_rdf)
        exact = (
            table_names_match
            and rdf_round_trip
            and all(bool(result["exact"]) for result in table_results.values())
        )
        partial_valid = (
            table_names_match
            and rdf_round_trip
            and all(bool(result["partial_valid"]) for result in table_results.values())
        )
        if exact:
            outcome = "FULL"
        elif partial_valid:
            outcome = "PARTIAL"
        else:
            outcome = "MISMATCH"

        errors = []
        if not table_names_match:
            errors.append("tables")
        if not rdf_round_trip:
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
            "tables": table_results,
            "errors": errors,
        }

    def dispose(self) -> None:
        self.engine.dispose()
