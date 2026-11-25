# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

"""Logical tables that a mapping defines as the result of a SQL query."""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from kgi.exceptions import UnsupportedMappingError
from kgi.schema import TableSchema
from kgi.utils import normalize_sql_identifier, undelimited_sql_identifier


@dataclass(frozen=True)
class LogicalQuery:
    """A query-defined logical table, named so that a database can hold its rows.

    The inverse rebuilds the rows of the query result, which is the logical table
    the mapping reads, and leaves the base tables behind the query alone.
    """

    name: str
    sql: str
    schema: TableSchema | None


def query_text(sql: str) -> str:
    """The query as the database runs it, without the surrounding whitespace and terminator."""
    return sql.strip().rstrip(";").strip()


def source_tables(sql: str) -> set[str]:
    """The base tables the query reads."""
    statement = sqlglot.parse_one(query_text(sql), read="postgres")
    return {table.name for table in statement.find_all(exp.Table)}


def _stored_name(identifier: exp.Identifier) -> str:
    return normalize_sql_identifier(identifier.sql(dialect="postgres"))


def recorded_query_columns(
    sql: str,
    references: Iterable[str],
    table_columns: Callable[[str], Sequence[str]] | None,
) -> dict[str, set[str]]:
    statement = sqlglot.parse_one(query_text(sql), read="postgres")
    qualifiers = {
        table.alias_or_name: _stored_name(table.this)
        for table in statement.find_all(exp.Table)
    }
    tables = set(qualifiers.values())
    named = {
        projection.alias_or_name: projection
        for projection in statement.expressions
        if not projection.is_star
    }

    def owner(name: str, qualifier: str) -> str:
        if qualifier:
            return qualifiers[qualifier]
        if len(tables) == 1:
            return next(iter(tables))
        if table_columns is None:
            raise UnsupportedMappingError(
                f"Without the source database, the table of column '{name}' "
                f"is unknown in the query: {query_text(sql)}"
            )
        return next(table for table in tables if name in table_columns(table))

    def place(column: exp.Column) -> tuple[str, str]:
        name = _stored_name(column.this)
        return owner(name, column.table), name

    placed: set[tuple[str, str]] = set()
    for reference in references:
        name = undelimited_sql_identifier(reference)
        label = resolve_reference(named, name)
        if label is None:
            placed.add((owner(name, ""), name))
            continue
        placed.update(place(column) for column in named[label].find_all(exp.Column))
    for equality in statement.find_all(exp.EQ):
        left, right = equality.left, equality.right
        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue
        sides = {place(left), place(right)}
        if len({table for table, _ in sides}) == 2:
            placed.update(sides)
    recorded: dict[str, set[str]] = {}
    for table, name in placed:
        recorded.setdefault(table, set()).add(name)
    return recorded


def resolve_reference(labels: Iterable[str], reference: str) -> str | None:
    """The result column a reference names.

    The label as written wins; otherwise the reference names the only label that
    matches it ignoring case, since an undelimited reference folds while the alias
    that names the result column keeps its case.
    """
    labels = tuple(labels)
    if reference in labels:
        return reference
    matches = [label for label in labels if label.casefold() == reference.casefold()]
    return matches[0] if len(matches) == 1 else None
