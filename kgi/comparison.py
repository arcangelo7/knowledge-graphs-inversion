# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import math
from collections import Counter
from enum import StrEnum
from typing import TypedDict

import pandas as pd

from kgi.core import MappingAnalysis, TableAnalysis


class TableContent(TypedDict):
    columns: list[str]
    data: list[list[object]]


DatabaseContent = dict[str, TableContent]
Row = tuple[object, ...]


class PartialLoss(StrEnum):
    COLUMNS_LOST = "columns_lost"
    ROWS_LOST = "rows_lost"
    MULTIPLICITY_LOST = "multiplicity_lost"
    TABLES_LOST = "tables_lost"


def _frame(table: TableContent) -> pd.DataFrame:
    return pd.DataFrame(table["data"], columns=pd.Index(table["columns"]))


def _cell(value: object) -> object:
    if value is None or value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _rows(frame: pd.DataFrame) -> Counter[Row]:
    """Count each row, ignoring column and row order.

    A relational instance is a multiset of rows, so equality of two tables is
    equality of these counters.
    """
    ordered = frame.reindex(sorted(frame.columns), axis=1)
    return Counter(
        tuple(_cell(value) for value in row)
        for row in ordered.itertuples(index=False, name=None)
    )


def _emitted_rows(
    source: pd.DataFrame, subject_reference_sets: tuple[frozenset[str], ...]
) -> pd.Series:
    emitted = pd.Series(False, index=source.index)
    for references in subject_reference_sets:
        columns = [column for column in references if column in source.columns]
        emitted |= source[columns].notna().all(axis=1)
    return emitted


def _expected_projection(
    source: pd.DataFrame, table: TableAnalysis
) -> tuple[pd.DataFrame, set[PartialLoss], list[str]]:
    """Reduce a source table to what the graph can give back.

    Each reduction step names the loss it causes, so the losses are a prediction
    the comparison then has to confirm.
    """
    losses: set[PartialLoss] = set()
    notes: list[str] = []

    emitted = _emitted_rows(source, table.subject_reference_sets)
    dropped = int((~emitted).sum())
    if dropped:
        losses.add(PartialLoss.ROWS_LOST)
        notes.append(f"{dropped} row(s) with NULL in a subject template column")

    lost_columns = [
        column for column in source.columns if column not in table.recoverable
    ]
    if lost_columns:
        losses.add(PartialLoss.COLUMNS_LOST)
        notes.append(f"columns not recovered: {', '.join(lost_columns)}")

    kept = [column for column in source.columns if column in table.recoverable]
    recovered = source.loc[emitted.to_numpy(), kept]
    projection = recovered.drop_duplicates()
    collapsed = len(recovered) - len(projection)
    if collapsed:
        losses.add(PartialLoss.MULTIPLICITY_LOST)
        notes.append(f"{collapsed} duplicate row(s) collapsed")
    return projection, losses, notes


def _table_reproduced(source: TableContent, dest: TableContent) -> bool:
    source_frame = _frame(source)
    return set(dest["columns"]) == set(source_frame.columns) and _rows(
        source_frame
    ) == _rows(_frame(dest))


def databases_identical(
    source_content: DatabaseContent, dest_content: DatabaseContent
) -> bool:
    return set(source_content) == set(dest_content) and all(
        _table_reproduced(source_content[name], dest_content[name])
        for name in source_content
    )


def _is_empty_cell(value: object) -> bool:
    cell = _cell(value)
    return cell is None or cell == ""


def compare_databases(
    source_content: DatabaseContent,
    dest_content: DatabaseContent,
    analysis: MappingAnalysis,
) -> tuple[bool, str, frozenset[PartialLoss]]:
    losses: set[PartialLoss] = set()
    notes: list[str] = []
    problems: list[str] = []

    for table_name in sorted(set(source_content) | set(dest_content)):
        if table_name not in source_content:
            problems.append(f"{table_name} (absent from the source database)")
            continue
        if table_name not in analysis:
            if table_name in dest_content:
                problems.append(
                    f"{table_name} (unmapped table present in the destination)"
                )
            else:
                losses.add(PartialLoss.TABLES_LOST)
                notes.append(f"{table_name}: unmapped table")
            continue
        if table_name not in dest_content:
            problems.append(f"{table_name} (missing from the destination database)")
            continue

        source_table = source_content[table_name]
        dest_table = dest_content[table_name]
        if _table_reproduced(source_table, dest_table):
            continue

        source_frame = _frame(source_table)
        dest_frame = _frame(dest_table)
        dest_columns = set(dest_table["columns"])
        projection, table_losses, table_notes = _expected_projection(
            source_frame, analysis[table_name]
        )
        projection_columns = set(projection.columns)
        # A column beyond the recoverable ones is fine if it was never actually
        # populated: some rows can be individually irrecoverable (e.g. an
        # ambiguous template split) even though the column is recoverable for
        # others, and that per-row gap must not be mistaken for unmapped data.
        extra_columns = dest_columns - projection_columns
        if projection_columns - dest_columns:
            problems.append(f"{table_name} (columns differ from the recoverable ones)")
            continue
        if extra_columns:
            populated_extra = [
                column
                for column in extra_columns
                if not dest_frame[column].map(_is_empty_cell).all()
            ]
            if populated_extra:
                problems.append(
                    f"{table_name} (columns differ from the recoverable ones)"
                )
                continue
            dest_frame = dest_frame.drop(columns=list(extra_columns))
        if _rows(projection) != _rows(dest_frame):
            problems.append(f"{table_name} (data differs from the expected projection)")
            continue
        losses |= table_losses
        notes.extend(f"{table_name}: {note}" for note in table_notes)

    if problems:
        return False, f"Unexplained differences: {'; '.join(problems)}", frozenset()
    if losses:
        return False, f"Characterised loss - {'; '.join(notes)}", frozenset(losses)
    return (
        True,
        "All tables in source and destination databases are identical",
        frozenset(),
    )
