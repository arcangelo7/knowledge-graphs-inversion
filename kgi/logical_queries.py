# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

"""Logical tables that a mapping defines as the result of a SQL query."""

from collections.abc import Iterable
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from kgi.schema import TableSchema


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
