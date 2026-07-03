# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import io
from typing import Union

import pandas as pd
from pyoxigraph import BlankNode, Literal, NamedNode, RdfFormat, Store, Triple

from kgi.constants import (
    RML_CHILD,
    RML_ITERATOR,
    RML_LOGICAL_SOURCE,
    RML_OLD_LOGICAL_SOURCE,
    RML_OLD_REFERENCE,
    RML_PARENT,
    RML_REFERENCE_FORMULATION,
    RML_REFERENCE_NODE,
    RML_SQL2008_TABLE,
    RML_SUBJECT_MAP,
    RML_TEMPLATE_NODE,
    RR_CHILD,
    RR_COLUMN,
    RR_LOGICAL_TABLE,
    RR_PARENT,
    RR_SUBJECT_MAP,
    RR_TABLE_NAME,
    RR_TEMPLATE,
    TEMPLATE_COLUMN_REGEX,
)

RdfSubject = Union[NamedNode, BlankNode, Triple]
RdfTerm = Union[NamedNode, BlankNode, Literal, Triple]


def _term_value(term: RdfTerm) -> str:
    assert isinstance(term, (NamedNode, BlankNode, Literal))
    return term.value


def parse_mapping(mapping_content: str) -> Store:
    store = Store()
    store.load(
        input=io.BytesIO(mapping_content.encode("utf-8")), format=RdfFormat.TURTLE
    )
    return store


def extract_columns_from_mapping(mapping_content: str) -> set[str]:
    store = parse_mapping(mapping_content)
    columns: set[str] = set()
    for predicate in (
        RR_COLUMN,
        RML_OLD_REFERENCE,
        RML_REFERENCE_NODE,
        RR_CHILD,
        RML_CHILD,
        RR_PARENT,
        RML_PARENT,
    ):
        for quad in store.quads_for_pattern(None, predicate, None):
            columns.add(_term_value(quad.object).strip('"'))
    for predicate in (RR_TEMPLATE, RML_TEMPLATE_NODE):
        for quad in store.quads_for_pattern(None, predicate, None):
            column_refs = TEMPLATE_COLUMN_REGEX.findall(_term_value(quad.object))
            columns.update(column_refs)
    return columns


def check_mapping_column_coverage(
    mapping_content: str,
    source_content: dict[str, dict[str, list[str]]],
) -> list[str]:
    mapped_columns = extract_columns_from_mapping(mapping_content)
    invertibility_issues = []
    for table_name, table_data in source_content.items():
        table_columns = set(table_data["columns"])
        missing_columns = table_columns - mapped_columns
        if missing_columns:
            missing_str = ", ".join(sorted(missing_columns))
            invertibility_issues.append(
                f"Table '{table_name}' has unmapped columns: {missing_str}"
            )
    return invertibility_issues


PARTIAL_COLUMNS_LOST = "columns_lost"
PARTIAL_ROWS_LOST = "rows_lost"
PARTIAL_MULTIPLICITY_LOST = "multiplicity_lost"
PARTIAL_TABLES_LOST = "tables_lost"


def _encode_outcome(subcategories: set[str]) -> str | None:
    if not subcategories:
        return None
    order = [
        PARTIAL_COLUMNS_LOST,
        PARTIAL_ROWS_LOST,
        PARTIAL_MULTIPLICITY_LOST,
        PARTIAL_TABLES_LOST,
    ]
    ordered = [s for s in order if s in subcategories]
    return "partial:" + ",".join(ordered)


def get_mapped_table_names(mapping_store: Store) -> set[str]:
    tables: set[str] = set()
    for quad in mapping_store.quads_for_pattern(None, RR_TABLE_NAME, None):
        tables.add(_term_value(quad.object).strip('"'))
    for quad in mapping_store.quads_for_pattern(
        None, RML_REFERENCE_FORMULATION, RML_SQL2008_TABLE
    ):
        iterator = _first_object(mapping_store, quad.subject, RML_ITERATOR)
        if iterator is not None:
            tables.add(_term_value(iterator).strip('"'))
    return tables


def _first_object(
    store: Store, subject: RdfSubject, predicate: NamedNode
) -> RdfTerm | None:
    for quad in store.quads_for_pattern(subject, predicate, None):
        return quad.object
    return None


def _subjects_of(store: Store, predicate: NamedNode, obj: RdfTerm) -> list[RdfSubject]:
    return [quad.subject for quad in store.quads_for_pattern(None, predicate, obj)]


def _logical_sources_for_table(
    mapping_store: Store, table_name: str
) -> list[RdfSubject]:
    sources = []
    for quad in mapping_store.quads_for_pattern(None, RR_TABLE_NAME, None):
        if _term_value(quad.object).strip('"') == table_name:
            sources.append(quad.subject)
    for quad in mapping_store.quads_for_pattern(
        None, RML_REFERENCE_FORMULATION, RML_SQL2008_TABLE
    ):
        iterator = _first_object(mapping_store, quad.subject, RML_ITERATOR)
        if iterator is not None and _term_value(iterator).strip('"') == table_name:
            sources.append(quad.subject)
    return sources


def find_subject_map_for_table(
    mapping_store: Store, table_name: str
) -> RdfSubject | None:
    for logical_source in _logical_sources_for_table(mapping_store, table_name):
        triples_maps: list[RdfSubject] = []
        for predicate in (RR_LOGICAL_TABLE, RML_OLD_LOGICAL_SOURCE, RML_LOGICAL_SOURCE):
            triples_maps = _subjects_of(mapping_store, predicate, logical_source)
            if triples_maps:
                break
        if not triples_maps:
            continue
        for predicate in (RR_SUBJECT_MAP, RML_SUBJECT_MAP):
            result = _first_object(mapping_store, triples_maps[0], predicate)
            if result is not None and isinstance(result, (NamedNode, BlankNode)):
                return result
    return None


def check_null_in_subject_template(
    mapping_store: Store,
    source_df: pd.DataFrame,
    table_name: str,
) -> tuple[str | None, bool]:
    subject_map = find_subject_map_for_table(mapping_store, table_name)
    if subject_map is None:
        return None, False
    template_quad = next(
        mapping_store.quads_for_pattern(subject_map, RR_TEMPLATE, None), None
    )
    if template_quad is None:
        template_quad = next(
            mapping_store.quads_for_pattern(subject_map, RML_TEMPLATE_NODE, None), None
        )
    if template_quad is None:
        return None, False
    column_refs = TEMPLATE_COLUMN_REGEX.findall(_term_value(template_quad.object))
    for col in column_refs:
        if col in source_df.columns and bool(source_df[col].isna().any()):
            null_count = int(source_df[col].isna().sum())
            return (
                f"{table_name} (PARTIALLY INVERTED: NULL values in subject template column "
                f"'{col}' cause {null_count} row(s) to be excluded from RDF)",
                True,
            )
    return None, False


def detect_non_invertible(
    mapping_store: Store,
    source_df: pd.DataFrame,
    table_name: str,
) -> tuple[str | None, bool]:
    null_msg, is_null = check_null_in_subject_template(
        mapping_store, source_df, table_name
    )
    if is_null:
        return null_msg, True
    return None, False


def analyze_duplicate_loss(
    source_df: pd.DataFrame,
    dest_df: pd.DataFrame,
    table_name: str,
) -> tuple[str | None, bool]:
    source_unique = source_df.drop_duplicates()
    dest_unique = dest_df.drop_duplicates()

    if source_unique.equals(dest_unique) and len(source_df) > len(dest_df):
        duplicate_rows = []
        for _, row in source_unique.iterrows():
            source_count = len(source_df[source_df.eq(row).all(axis=1)])
            dest_count = len(dest_df[dest_df.eq(row).all(axis=1)])
            if source_count > dest_count:
                duplicate_rows.append((source_count, dest_count, dict(row)))

        if duplicate_rows:
            duplicate_info = "; ".join(
                [
                    f"Row {row} appears {src_cnt} times in source but {dst_cnt} times in destination"
                    for src_cnt, dst_cnt, row in duplicate_rows
                ]
            )
            message = (
                f"{table_name} (PARTIALLY INVERTED: Duplicate rows lost during inversion - {duplicate_info}. "
                "Consider adding unique identifiers to your mapping template to preserve row distinctness)"
            )
            return message, True

    return None, False


def compare_databases(
    source_content: dict[str, dict[str, list[str]]],
    dest_content: dict[str, dict[str, list[str]]],
    mapping_content: str | None = None,
) -> tuple[bool, str, str | None]:
    if not source_content and not dest_content:
        return True, "Both databases are empty - comparison successful", None
    if not source_content or not dest_content:
        return False, "One database is empty while the other is not", None

    mapping_graph = parse_mapping(mapping_content) if mapping_content else None

    source_tables = set(source_content.keys())
    dest_tables = set(dest_content.keys())
    missing_from_dest = source_tables - dest_tables

    mismatched_tables = []
    subcategories: set[str] = set()

    if missing_from_dest:
        if mapping_graph:
            mapped_tables = get_mapped_table_names(mapping_graph)
            unmapped_tables = {t for t in missing_from_dest if t not in mapped_tables}
            if unmapped_tables == missing_from_dest:
                unmapped_str = ", ".join(sorted(unmapped_tables))
                mismatched_tables.append(
                    f"PARTIALLY INVERTED: Unmapped tables: {unmapped_str}"
                )
                subcategories.add(PARTIAL_TABLES_LOST)
            else:
                return (
                    False,
                    "Tables in source and destination databases do not match",
                    None,
                )
        else:
            return (
                False,
                "Tables in source and destination databases do not match",
                None,
            )

    common_tables = source_tables & dest_tables
    for table_name in common_tables:
        source_table = source_content[table_name]
        dest_table = dest_content[table_name]

        if set(source_table["columns"]) != set(dest_table["columns"]):
            mismatched_tables.append(f"{table_name} (columns mismatch)")
            continue

        source_df = pd.DataFrame(
            source_table["data"], columns=pd.Index(source_table["columns"])
        )
        dest_df = pd.DataFrame(
            dest_table["data"], columns=pd.Index(dest_table["columns"])
        )

        if source_df.empty and dest_df.empty:
            continue

        source_df = source_df.dropna(how="all")
        dest_df = dest_df.dropna(how="all")
        source_df = source_df.reindex(sorted(source_df.columns), axis=1)
        dest_df = dest_df.reindex(sorted(dest_df.columns), axis=1)
        source_df = source_df.reset_index(drop=True)
        dest_df = dest_df.reset_index(drop=True)
        source_df = source_df.sort_values(by=source_df.columns.tolist()).reset_index(
            drop=True
        )
        dest_df = dest_df.sort_values(by=dest_df.columns.tolist()).reset_index(
            drop=True
        )

        if not source_df.equals(dest_df):
            resolved = False
            if len(source_df) > len(dest_df):
                duplicate_analysis, is_dup_issue = analyze_duplicate_loss(
                    source_df, dest_df, table_name
                )
                if duplicate_analysis:
                    mismatched_tables.append(duplicate_analysis)
                    if is_dup_issue:
                        subcategories.add(PARTIAL_MULTIPLICITY_LOST)
                    resolved = True

            if not resolved and mapping_graph:
                issue_msg, is_issue = detect_non_invertible(
                    mapping_graph, source_df, table_name
                )
                if is_issue:
                    mismatched_tables.append(issue_msg)
                    subcategories.add(PARTIAL_ROWS_LOST)
                    resolved = True

            if not resolved:
                mismatched_tables.append(f"{table_name} (data mismatch)")

    if mismatched_tables:
        message = f"Mismatched tables: {', '.join(mismatched_tables)}"

        if mapping_content:
            invertibility_issues = check_mapping_column_coverage(
                mapping_content, source_content
            )
            if invertibility_issues:
                invertibility_message = "; ".join(invertibility_issues)
                message += f" (PARTIALLY INVERTED: {invertibility_message})"
                subcategories.add(PARTIAL_COLUMNS_LOST)

        outcome = _encode_outcome(subcategories)
        if outcome is not None:
            return False, message, outcome
        return False, message, None

    return True, "All tables in source and destination databases are identical", None
