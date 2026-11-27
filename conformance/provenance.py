# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import re
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast
from urllib.parse import quote

import pandas as pd
from pyoxigraph import (
    BlankNode,
    DefaultGraph,
    Literal,
    NamedNode,
    Quad,
    RdfFormat,
    parse,
    serialize,
)
from sqlalchemy import (
    Boolean,
    Column,
    Connection,
    Float,
    MetaData,
    Table,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.types import NullType, TypeEngine

from conformance.database import table_content
from kgi.comparison import DatabaseContent
from kgi.constants import (
    RDF_TYPE,
    REF_TEMPLATE_REGEX,
    RML_BLANK_NODE,
    RML_CONSTANT,
    RML_DATATYPE_MAP,
    RML_DEFAULT_GRAPH,
    RML_IRI,
    RML_LANGUAGE_MAP,
    RML_PARENT_TRIPLES_MAP,
    RML_REFERENCE,
    RML_TEMPLATE,
    TRIPLES_MAP_IRI,
)
from kgi.core import (
    _join_conditions,
    _load_mapping_rules,
    mapped_columns,
    omitted_columns,
)
from kgi.logical_queries import LogicalQuery, query_text, resolve_reference
from kgi.provenance import (
    PROV_HAD_PRIMARY_SOURCE,
    PROV_WAS_DERIVED_FROM,
    RDF_OBJECT,
    RDF_PREDICATE,
    RDF_STATEMENT,
    RDF_SUBJECT,
    SOURCE_NAMESPACE,
    Provenance,
    column_property,
    table_class,
)
from kgi.utils import undelimited_sql_identifier

XSD = "http://www.w3.org/2001/XMLSchema#"
IRI_UNRESERVED = "-._~"
# RMLMapper prefixes a relative IRI with the base IRI, which is this one when the
# mapping declares none
DEFAULT_BASE_IRI = "http://example.com"
ABSOLUTE_IRI = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")


@dataclass(frozen=True)
class BlankLabel:
    label: str


Term = NamedNode | BlankLabel | Literal


class ProvenanceCoverageError(Exception):
    pass


@dataclass(eq=False)
class SourceRow:
    table: str
    iri: NamedNode
    values: dict[str, object]


@dataclass(eq=False)
class LogicalTable:
    name: str
    columns: list[Column]
    rows: list[SourceRow]


@dataclass(eq=False)
class LogicalRow:
    values: dict[str, object]
    types: dict[str, TypeEngine]
    sources: tuple[SourceRow, ...]


def _row_iri(table: str, key: dict[str, object] | None, position: int) -> NamedNode:
    if key is None:
        return NamedNode(f"{table_class(table)}/{position}")
    identity = ";".join(
        f"{quote(column, safe='')}={quote(str(value), safe='')}"
        for column, value in key.items()
    )
    return NamedNode(f"{table_class(table)}/{identity}")


def _raw_value(value: object) -> object:
    return bytes(value) if isinstance(value, memoryview) else value


def _read_base_tables(connection: Connection) -> dict[str, LogicalTable]:
    inspector = inspect(connection)
    metadata = MetaData()
    tables: dict[str, LogicalTable] = {}
    for name in inspector.get_table_names():
        table = Table(name, metadata, autoload_with=connection)
        primary_key = inspector.get_pk_constraint(name)["constrained_columns"]
        names = [column.name for column in table.columns]
        rows = []
        for position, record in enumerate(connection.execute(select(table)), start=1):
            values = dict(zip(names, (_raw_value(value) for value in record)))
            key = {column: values[column] for column in primary_key} or None
            rows.append(SourceRow(name, _row_iri(name, key, position), values))
        tables[name] = LogicalTable(name, list(table.columns), rows)
    return tables


def _base_table(tables: dict[str, LogicalTable], name: str) -> LogicalTable:
    # The database folds an undelimited name, so the written one may not match
    return tables[str(resolve_reference(tables, undelimited_sql_identifier(name)))]


def _unique(rows: Iterable[SourceRow]) -> tuple[SourceRow, ...]:
    return tuple({id(row): row for row in rows}.values())


def _query_table(connection: Connection, name: str, sql: str) -> LogicalTable:
    # A query result has no primary key: its rows are known by their position
    result = connection.execute(text(query_text(sql)))
    labels = list(result.keys())
    rows = [
        SourceRow(
            name,
            _row_iri(name, None, position),
            dict(zip(labels, (_raw_value(value) for value in record))),
        )
        for position, record in enumerate(result, start=1)
    ]
    return LogicalTable(name, [Column(label, NullType()) for label in labels], rows)


def _label(row: LogicalRow, reference: str) -> str:
    return str(resolve_reference(row.values, undelimited_sql_identifier(reference)))


def _value(row: LogicalRow, reference: str) -> object:
    return row.values[_label(row, reference)]


def _natural(row: LogicalRow, reference: str) -> tuple[str, str | None] | None:
    label = _label(row, reference)
    value = row.values[label]
    if value is None:
        return None
    sql_type = row.types[label]
    if isinstance(sql_type, Boolean) or (
        isinstance(sql_type, TINYINT) and sql_type.display_width == 1
    ):
        return ("true" if value else "false"), f"{XSD}boolean"
    if isinstance(sql_type, Float):
        return _canonical_double(float(cast(float, value))), f"{XSD}double"
    return _natural_value(value)


def _canonical_double(value: float) -> str:
    sign, digits, exponent = Decimal(repr(value)).normalize().as_tuple()
    if digits == (0,):
        return "0.0E0"
    mantissa = "".join(map(str, digits))
    scientific_exponent = len(digits) - 1 + int(exponent)
    fraction = mantissa[1:] or "0"
    return f"{'-' if sign else ''}{mantissa[0]}.{fraction}E{scientific_exponent}"


def _natural_value(value: object) -> tuple[str, str | None]:
    if isinstance(value, bool):
        return ("true" if value else "false"), f"{XSD}boolean"
    if isinstance(value, int):
        return str(value), f"{XSD}integer"
    if isinstance(value, Decimal):
        return str(value), f"{XSD}decimal"
    if isinstance(value, float):
        return _canonical_double(value), f"{XSD}double"
    if isinstance(value, datetime):
        return value.isoformat(), f"{XSD}dateTime"
    if isinstance(value, date):
        return value.isoformat(), f"{XSD}date"
    if isinstance(value, bytes):
        return value.hex().upper(), f"{XSD}hexBinary"
    return str(value), None


def _iri_safe(lexical: str) -> str:
    return "".join(
        character
        if character in IRI_UNRESERVED
        or (character.isascii() and character.isalnum())
        or ord(character) >= 0xA0
        else quote(character, safe="")
        for character in lexical
    )


def _expand(template: str, row: LogicalRow, encode: bool) -> str | None:
    values = []
    for reference in re.findall(REF_TEMPLATE_REGEX, template):
        natural = _natural(row, reference)
        if natural is None:
            return None
        values.append(_iri_safe(natural[0]) if encode else natural[0])
    parts = re.split(REF_TEMPLATE_REGEX, template)[::2]
    expanded = parts[0]
    for value, part in zip(values, parts[1:]):
        expanded += value + part
    return expanded.replace("\\{", "{").replace("\\}", "}")


def _term(
    map_type: object, map_value: object, termtype: object, row: LogicalRow, base: str
) -> Term | None:
    map_value = str(map_value)
    if map_type == RML_CONSTANT:
        return NamedNode(map_value) if termtype == RML_IRI else Literal(map_value)
    if map_type == RML_TEMPLATE:
        lexical = _expand(map_value, row, encode=termtype == RML_IRI)
    elif map_type == RML_REFERENCE:
        natural = _natural(row, map_value)
        if natural is None:
            return None
        lexical, datatype = natural
        if termtype not in (RML_IRI, RML_BLANK_NODE):
            return Literal(lexical, datatype=NamedNode(datatype) if datatype else None)
    else:
        raise ValueError(f"Unsupported term map type: {map_type}")
    if lexical is None:
        return None
    if termtype == RML_IRI:
        return NamedNode(lexical if ABSOLUTE_IRI.match(lexical) else base + lexical)
    if termtype == RML_BLANK_NODE:
        return BlankLabel(lexical)
    return Literal(lexical)


def _object(rule: pd.Series, row: LogicalRow, base: str) -> Term | None:
    term = _term(
        rule["object_map_type"],
        rule["object_map_value"],
        rule["object_termtype"],
        row,
        base,
    )
    if not isinstance(term, Literal):
        return term
    if rule["lang_datatype"] == RML_LANGUAGE_MAP:
        language = _term(
            rule["lang_datatype_map_type"],
            rule["lang_datatype_map_value"],
            "",
            row,
            base,
        )
        if not isinstance(language, Literal):
            return None
        return Literal(term.value, language=language.value)
    if rule["lang_datatype"] == RML_DATATYPE_MAP:
        datatype = _term(
            rule["lang_datatype_map_type"],
            rule["lang_datatype_map_value"],
            RML_IRI,
            row,
            base,
        )
        if not isinstance(datatype, NamedNode):
            return None
        return Literal(term.value, datatype=datatype)
    return term


def _subject(rule: pd.Series, row: LogicalRow, base: str) -> Term | None:
    return _term(
        rule["subject_map_type"],
        rule["subject_map_value"],
        rule["subject_termtype"],
        row,
        base,
    )


def _graph(rule: pd.Series, row: LogicalRow, base: str) -> Term | DefaultGraph | None:
    if rule["graph_map_value"] == RML_DEFAULT_GRAPH:
        return DefaultGraph()
    return _term(rule["graph_map_type"], rule["graph_map_value"], RML_IRI, row, base)


def _joined_rows(
    rule: pd.Series, children: list[LogicalRow], parents: list[LogicalRow]
) -> Iterable[tuple[LogicalRow, LogicalRow]]:
    conditions = _join_conditions(rule)
    if not conditions:
        return zip(children, parents)
    by_key: dict[tuple[object, ...], list[LogicalRow]] = defaultdict(list)
    for parent in parents:
        by_key[tuple(_value(parent, column) for _, column in conditions)].append(parent)
    pairs = []
    for child in children:
        key = tuple(_value(child, column) for column, _ in conditions)
        # NULL never joins
        if None in key:
            continue
        pairs += [(child, parent) for parent in by_key[key]]
    return pairs


@dataclass(frozen=True)
class Candidate:
    subject: Term
    predicate: Term | None
    object: Term
    graph: Term | DefaultGraph
    rows: tuple[SourceRow, ...]
    triples_map: str


QuadTerm = NamedNode | BlankNode | Literal | DefaultGraph


def _match(
    candidates: list[Candidate], produced: set[Quad]
) -> list[tuple[Quad, Candidate]]:
    # A store relabels blank nodes, so a blank node the mapping labels is found by
    # its position among the produced quads; once found, its label names it in
    # every other quad
    blank_nodes = {
        term
        for quad in produced
        for term in (quad.subject, quad.object)
        if isinstance(term, BlankNode)
    }
    by_subject: dict[tuple[object, ...], list[Quad]] = defaultdict(list)
    by_object: dict[tuple[object, ...], list[Quad]] = defaultdict(list)
    by_predicate: dict[tuple[object, ...], list[Quad]] = defaultdict(list)
    for quad in produced:
        by_subject[quad.subject, quad.predicate, quad.graph_name].append(quad)
        by_object[quad.predicate, quad.object, quad.graph_name].append(quad)
        by_predicate[quad.predicate, quad.graph_name].append(quad)
    bound: dict[str, BlankNode] = {}

    def resolve(term: Term | DefaultGraph | None) -> QuadTerm | None:
        if not isinstance(term, BlankLabel):
            return term
        if term.label in bound:
            return bound[term.label]
        try:
            labelled = BlankNode(term.label)
        except ValueError:
            return None
        return labelled if labelled in blank_nodes else None

    def lookup(
        subject: QuadTerm | None,
        predicate: QuadTerm | None,
        obj: QuadTerm | None,
        graph: QuadTerm | None,
    ) -> list[Quad]:
        if predicate is None:
            return []
        if subject is not None and obj is not None:
            pool = by_subject[subject, predicate, graph]
            return [quad for quad in pool if quad.object == obj]
        if subject is not None:
            pool = by_subject[subject, predicate, graph]
        elif obj is not None:
            pool = by_object[predicate, obj, graph]
        else:
            pool = by_predicate[predicate, graph]
        # An unresolved blank node matches any blank node in its position
        return [
            quad
            for quad in pool
            if (subject is not None or isinstance(quad.subject, BlankNode))
            and (obj is not None or isinstance(quad.object, BlankNode))
        ]

    matched: list[tuple[Quad, Candidate]] = []
    pending = candidates
    progress = True
    while pending and progress:
        progress = False
        unresolved = []
        for candidate in pending:
            subject, predicate, obj, graph = (
                resolve(term)
                for term in (
                    candidate.subject,
                    candidate.predicate,
                    candidate.object,
                    candidate.graph,
                )
            )
            matches = lookup(subject, predicate, obj, graph)
            if (subject is None or obj is None) and len(matches) != 1:
                unresolved.append(candidate)
                continue
            for quad in matches:
                for term, found in (
                    (candidate.subject, quad.subject),
                    (candidate.object, quad.object),
                ):
                    if isinstance(term, BlankLabel) and isinstance(found, BlankNode):
                        bound[term.label] = found
                matched.append((quad, candidate))
            progress = True
        pending = unresolved
    return matched


def _provenance_lexical(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, bytes):
        return value.hex().upper()
    return str(value)


def _base_iri(mapping: Path) -> str:
    declaration = re.search(
        r"(?:@base|BASE)\s*<([^>]*)>", mapping.read_text(encoding="utf-8")
    )
    return declaration.group(1) if declaration else DEFAULT_BASE_IRI


def _logical_tables(
    connection: Connection, mappings: pd.DataFrame, queries: dict[str, LogicalQuery]
) -> dict[str, LogicalTable]:
    tables = _read_base_tables(connection)
    return {
        name: _query_table(connection, name, queries[name].sql)
        if name in queries
        else _base_table(tables, name)
        for name in map(str, mappings["logical_source_value"].unique())
    }


def _logical_rows(tables: dict[str, LogicalTable]) -> dict[str, list[LogicalRow]]:
    logical_rows: dict[str, list[LogicalRow]] = {}
    for name, table in tables.items():
        types = {column.name: column.type for column in table.columns}
        logical_rows[name] = [
            LogicalRow(row.values, types, (row,)) for row in table.rows
        ]
    return logical_rows


def _parent_rule(mappings: pd.DataFrame, rule: pd.Series) -> pd.Series:
    return mappings[mappings["triples_map_id"] == rule["object_map_value"]].iloc[0]


def _candidates(
    mappings: pd.DataFrame, logical_rows: dict[str, list[LogicalRow]], base: str
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for _, rule in mappings.iterrows():
        # A triples map without predicate-object maps produces no triple of its own
        if not isinstance(rule["predicate_map_type"], str):
            continue
        children = logical_rows[str(rule["logical_source_value"])]
        if rule["object_map_type"] == RML_PARENT_TRIPLES_MAP:
            parent_rule = _parent_rule(mappings, rule)
            parent_source = str(parent_rule["logical_source_value"])
            pairs = [
                (
                    child,
                    _subject(parent_rule, parent, base),
                    child.sources + parent.sources,
                )
                for child, parent in _joined_rows(
                    rule, children, logical_rows[parent_source]
                )
            ]
        else:
            pairs = [(row, _object(rule, row, base), row.sources) for row in children]
        for row, obj, sources in pairs:
            subject = _subject(rule, row, base)
            predicate = _term(
                rule["predicate_map_type"],
                rule["predicate_map_value"],
                RML_IRI,
                row,
                base,
            )
            graph_name = _graph(rule, row, base)
            if subject is None or obj is None or graph_name is None:
                continue
            candidates.append(
                Candidate(
                    subject,
                    predicate,
                    obj,
                    graph_name,
                    _unique(sources),
                    str(rule[TRIPLES_MAP_IRI]),
                )
            )
    return candidates


ReifiedTriples = dict[tuple[Quad, tuple[str, ...], str], tuple[SourceRow, ...]]


def _provenance_quads(
    reified_triples: ReifiedTriples,
    source_rows: tuple[SourceRow, ...],
    described_columns: dict[str, set[str]],
) -> list[Quad]:
    quads: list[Quad] = []
    for index, ((quad, _, triples_map), rows) in enumerate(
        reified_triples.items(), start=1
    ):
        reified_triple = NamedNode(f"{SOURCE_NAMESPACE}statement/{index}")
        quads += [
            Quad(reified_triple, RDF_TYPE, RDF_STATEMENT),
            Quad(reified_triple, RDF_SUBJECT, quad.subject),
            Quad(reified_triple, RDF_PREDICATE, quad.predicate),
            Quad(reified_triple, RDF_OBJECT, quad.object),
            Quad(reified_triple, PROV_WAS_DERIVED_FROM, NamedNode(triples_map)),
        ]
        quads += [
            Quad(reified_triple, PROV_HAD_PRIMARY_SOURCE, row.iri) for row in rows
        ]
    for row in source_rows:
        quads.append(Quad(row.iri, RDF_TYPE, NamedNode(table_class(row.table))))
        quads += [
            Quad(
                row.iri,
                NamedNode(column_property(row.table, column)),
                Literal(_provenance_lexical(value)),
            )
            for column, value in row.values.items()
            if column in described_columns[row.table] and value is not None
        ]
    return quads


def _described_content(
    source_rows: tuple[SourceRow, ...],
    mapped: dict[str, set[str]],
    tables: dict[str, LogicalTable],
) -> DatabaseContent:
    content: DatabaseContent = {}
    for table_name in dict.fromkeys(row.table for row in source_rows):
        rows = [row for row in source_rows if row.table == table_name]
        columns = [
            column
            for column in tables[table_name].columns
            if column.name in mapped[table_name]
        ]
        content[table_name] = table_content(
            columns, [[row.values[column.name] for column in columns] for row in rows]
        )
    return content


@dataclass(frozen=True)
class ExpectedRecovery:
    # What an inversion that reads the provenance must give back
    described: DatabaseContent
    logical_queries: dict[str, str]
    logical_tables: frozenset[str]


@dataclass(eq=False)
class ReifiedGraph:
    mappings: pd.DataFrame
    queries: dict[str, LogicalQuery]
    tables: dict[str, LogicalTable]
    produced: set[Quad]
    reified_triples: ReifiedTriples
    source_rows: tuple[SourceRow, ...]
    mapped: dict[str, set[str]]


def _column_labels(
    columns_by_source: Mapping[str, Collection[str]], tables: dict[str, LogicalTable]
) -> dict[str, set[str]]:
    by_table: dict[str, set[str]] = defaultdict(set)
    for source, columns in columns_by_source.items():
        table = tables[source]
        labels = [column.name for column in table.columns]
        by_table[table.name] |= {
            cast(str, resolve_reference(labels, column)) for column in columns
        }
    return by_table


def reify_graph(mapping: str | Path, graph: Path, source_db_url: str) -> ReifiedGraph:
    mappings, _, queries = _load_mapping_rules(
        mapping, str(graph), source_db_url, keep_self_joins=True
    )
    produced = set(parse(path=str(graph), format=RdfFormat.N_QUADS))

    engine = create_engine(source_db_url)
    try:
        with engine.connect() as connection:
            tables = _logical_tables(connection, mappings, queries)
    finally:
        engine.dispose()

    candidates = _candidates(mappings, _logical_rows(tables), _base_iri(Path(mapping)))
    reified_triples: ReifiedTriples = {
        (
            quad,
            tuple(row.iri.value for row in candidate.rows),
            candidate.triples_map,
        ): candidate.rows
        for quad, candidate in _match(candidates, produced)
    }
    uncovered = produced - {quad for quad, _, _ in reified_triples}
    if uncovered:
        listing = "\n".join(sorted(str(quad) for quad in uncovered))
        raise ProvenanceCoverageError(f"Triples without provenance:\n{listing}")

    return ReifiedGraph(
        mappings,
        queries,
        tables,
        produced,
        reified_triples,
        _unique(row for rows in reified_triples.values() for row in rows),
        _column_labels(mapped_columns(mappings), tables),
    )


def expected_recovery(reified: ReifiedGraph) -> ExpectedRecovery:
    return ExpectedRecovery(
        _described_content(
            reified.source_rows,
            reified.mapped,
            {table.name: table for table in reified.tables.values()},
        ),
        {name: query.sql for name, query in reified.queries.items()},
        frozenset(reified.tables),
    )


def write_provenance(
    reified: ReifiedGraph, output: Path, provenance: Provenance
) -> None:
    described_columns = reified.mapped
    if provenance is Provenance.SELECTIVE:
        omitted = _column_labels(omitted_columns(reified.mappings), reified.tables)
        described_columns = {
            table: columns - omitted[table] for table, columns in reified.mapped.items()
        }
    quads = [
        *reified.produced,
        *_provenance_quads(
            reified.reified_triples, reified.source_rows, described_columns
        ),
    ]
    output.write_bytes(serialize(quads, format=RdfFormat.N_QUADS) or b"")
