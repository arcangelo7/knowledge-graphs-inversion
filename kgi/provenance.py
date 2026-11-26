# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC


from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote

import pandas as pd
from pyoxigraph import NamedNode, Quad, QuerySolution

from kgi.base import Endpoint
from kgi.constants import RDF_TYPE
from kgi.utils import url_decode

SOURCE_NAMESPACE = "http://src/"
RDF_STATEMENT = NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#Statement")
RDF_SUBJECT = NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#subject")
RDF_PREDICATE = NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#predicate")
RDF_OBJECT = NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#object")
PROV_HAD_PRIMARY_SOURCE = NamedNode("http://www.w3.org/ns/prov#hadPrimarySource")
PROV_WAS_DERIVED_FROM = NamedNode("http://www.w3.org/ns/prov#wasDerivedFrom")
REIFICATION_PREDICATES = frozenset(
    {
        RDF_SUBJECT,
        RDF_PREDICATE,
        RDF_OBJECT,
        PROV_HAD_PRIMARY_SOURCE,
        PROV_WAS_DERIVED_FROM,
    }
)
_CHUNK_SIZE = 10_000


class Provenance(Enum):
    FULL = "full"
    SELECTIVE = "selective"


@dataclass(frozen=True)
class TermLocation:
    triples_map: str
    predicate: str | None


@dataclass(frozen=True)
class ColumnOccurrence:
    location: TermLocation
    prefix: str
    suffix: str
    iri: bool

    def extract(self, term: str) -> str:
        body = term[: len(term) - len(self.suffix)]
        if not self.iri:
            return body[len(self.prefix) :]
        return url_decode(body[body.rindex(self.prefix) + len(self.prefix) :])


def table_class(table: str) -> str:
    return f"{SOURCE_NAMESPACE}{quote(table, safe='')}"


def column_property(table: str, column: str) -> str:
    return f"{table_class(table)}#{quote(column, safe='')}"


def is_reification(quad: Quad) -> bool:
    return quad.predicate in REIFICATION_PREDICATES or (
        quad.predicate == RDF_TYPE and quad.object == RDF_STATEMENT
    )


def _term_subquery(location: TermLocation, variable: str) -> str:
    position = (
        f"<{RDF_SUBJECT.value}> ?term"
        if location.predicate is None
        else f"<{RDF_PREDICATE.value}> <{location.predicate}> ; <{RDF_OBJECT.value}> ?term"
    )
    # One term per row: every reified triple a triples map derives from a row has
    # the same subject, and the predicate identifies the object map
    return (
        f"OPTIONAL {{ SELECT ?row (SAMPLE(?term) AS ?{variable}) WHERE {{ "
        f"?reified_triple <{PROV_HAD_PRIMARY_SOURCE.value}> ?row ; "
        f"<{PROV_WAS_DERIVED_FROM.value}> <{location.triples_map}> ; {position} "
        f"}} GROUP BY ?row }}"
    )


def described_rows(
    endpoint: Endpoint,
    table: str,
    columns: Sequence[str],
    omitted: dict[str, list[ColumnOccurrence]],
) -> Iterator[pd.DataFrame]:
    if not columns:
        return
    locations = list(
        dict.fromkeys(
            occurrence.location
            for column_occurrences in omitted.values()
            for occurrence in column_occurrences
        )
    )
    location_variables = {
        location: f"term{index}" for index, location in enumerate(locations)
    }
    described = {
        column: f"value{index}"
        for index, column in enumerate(columns)
        if column not in omitted
    }
    patterns = [
        f"OPTIONAL {{ ?row <{column_property(table, column)}> ?{variable} }}"
        for column, variable in described.items()
    ] + [
        _term_subquery(location, variable)
        for location, variable in location_variables.items()
    ]
    variables = [*described.values(), *location_variables.values()]
    solutions = endpoint.query(
        f"SELECT {' '.join(f'?{variable}' for variable in variables)} "
        f"WHERE {{ ?row a <{table_class(table)}> . {' '.join(patterns)} }}"
    )

    def column_value(solution: QuerySolution, column: str) -> str | None:
        if column in described:
            term = solution[described[column]]
            return None if term is None else term.value
        for occurrence in omitted[column]:
            term = solution[location_variables[occurrence.location]]
            if term is not None:
                return occurrence.extract(term.value)
        return None

    # The values are plain strings, which the schema types afterwards
    chunk: list[list[str | None]] = [[] for _ in columns]
    for solution in solutions:
        for values, column in zip(chunk, columns):
            values.append(column_value(solution, column))
        if len(chunk[0]) == _CHUNK_SIZE:
            yield pd.DataFrame(dict(zip(columns, chunk)))
            chunk = [[] for _ in columns]
    if chunk[0]:
        yield pd.DataFrame(dict(zip(columns, chunk)))
