# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC


from collections.abc import Iterator, Sequence
from enum import Enum
from urllib.parse import quote

import pandas as pd
from pyoxigraph import NamedNode, Quad

from kgi.base import Endpoint
from kgi.constants import RDF_TYPE

SOURCE_NAMESPACE = "http://src/"
RDF_STATEMENT = NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#Statement")
REIFICATION_PREDICATES = frozenset(
    {
        NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#subject"),
        NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#predicate"),
        NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#object"),
        NamedNode("http://www.w3.org/ns/prov#hadPrimarySource"),
        NamedNode("http://www.w3.org/ns/prov#wasDerivedFrom"),
    }
)
_CHUNK_SIZE = 10_000


class Provenance(Enum):
    FULL = "full"


def table_class(table: str) -> str:
    return f"{SOURCE_NAMESPACE}{quote(table, safe='')}"


def column_property(table: str, column: str) -> str:
    return f"{table_class(table)}#{quote(column, safe='')}"


def is_reification(quad: Quad) -> bool:
    return quad.predicate in REIFICATION_PREDICATES or (
        quad.predicate == RDF_TYPE and quad.object == RDF_STATEMENT
    )


def described_rows(
    endpoint: Endpoint, table: str, columns: Sequence[str]
) -> Iterator[pd.DataFrame]:
    if not columns:
        return
    variables = [f"c{index}" for index in range(len(columns))]
    optionals = " ".join(
        f"OPTIONAL {{ ?row <{column_property(table, column)}> ?{variable} }}"
        for column, variable in zip(columns, variables)
    )
    solutions = endpoint.query(
        f"SELECT {' '.join(f'?{variable}' for variable in variables)} "
        f"WHERE {{ ?row a <{table_class(table)}> . {optionals} }}"
    )
    # The values are plain literals, which the schema types afterwards
    chunk: list[list[str | None]] = [[] for _ in columns]
    for solution in solutions:
        for values, variable in zip(chunk, variables):
            term = solution[variable]
            values.append(None if term is None else term.value)
        if len(chunk[0]) == _CHUNK_SIZE:
            yield pd.DataFrame(dict(zip(columns, chunk)))
            chunk = [[] for _ in columns]
    if chunk[0]:
        yield pd.DataFrame(dict(zip(columns, chunk)))
