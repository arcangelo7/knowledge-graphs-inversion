# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

from collections import Counter

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

RowFingerprint = tuple[tuple[str, object], ...]


def _row_fingerprint(row: tuple[object, ...]) -> RowFingerprint:
    return tuple((type(value).__qualname__, value) for value in row)


def _row_counts(data: pd.DataFrame) -> Counter[RowFingerprint]:
    return Counter(
        _row_fingerprint(tuple(row)) for row in data.itertuples(index=False, name=None)
    )


def validate_exact_table(
    original: pd.DataFrame,
    reconstructed: pd.DataFrame,
    scenario_name: str,
) -> dict[str, object]:
    original_columns = list(original.columns)
    reconstructed_columns = list(reconstructed.columns)
    columns_match = original_columns == reconstructed_columns
    rows_match = len(original) == len(reconstructed)

    if columns_match:
        original_counts = _row_counts(original)
        reconstructed_counts = _row_counts(reconstructed)
        values_match = set(original_counts) == set(reconstructed_counts)
        multiplicities_match = original_counts == reconstructed_counts
    else:
        values_match = False
        multiplicities_match = False

    checks = {
        "columns": columns_match,
        "rows": rows_match,
        "values": values_match,
        "multiplicities": multiplicities_match,
    }
    errors = [name for name, passed in checks.items() if not passed]
    validation_passed = all(checks.values())

    return {
        "scenario": scenario_name,
        "validation_passed": validation_passed,
        "outcome": "FULL" if validation_passed else "MISMATCH",
        "checks": checks,
        "errors": errors,
        "metrics": {
            "original_rows": len(original),
            "reconstructed_rows": len(reconstructed),
            "original_columns": original_columns,
            "reconstructed_columns": reconstructed_columns,
        },
    }


class KrownValidator:
    def __init__(self, connection_string: str):
        self.engine: Engine = create_engine(connection_string)

    def validate_inversion(
        self,
        original_table: str,
        reconstructed_table: str,
        scenario_name: str,
    ) -> dict[str, object]:
        with self.engine.connect() as connection:
            original = pd.read_sql(f'SELECT * FROM "{original_table}"', connection)
            reconstructed = pd.read_sql(
                f'SELECT * FROM "{reconstructed_table}"', connection
            )
        return validate_exact_table(original, reconstructed, scenario_name)

    def dispose(self) -> None:
        self.engine.dispose()
