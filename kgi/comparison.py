import io
import re

import pandas as pd
from pyoxigraph import BlankNode, Literal, NamedNode, RdfFormat, Store, Triple

type RdfSubject = NamedNode | BlankNode | Triple
type RdfTerm = NamedNode | BlankNode | Literal | Triple

TEMPLATE_COLUMN_REGEX = re.compile(r'\{\\?"?\'?([^"\'{}\\]+)\\?"?\'?\}')

RR_COLUMN = NamedNode("http://www.w3.org/ns/r2rml#column")
RR_TEMPLATE = NamedNode("http://www.w3.org/ns/r2rml#template")
RR_CHILD = NamedNode("http://www.w3.org/ns/r2rml#child")
RR_PARENT = NamedNode("http://www.w3.org/ns/r2rml#parent")
RR_TABLE_NAME = NamedNode("http://www.w3.org/ns/r2rml#tableName")
RR_LOGICAL_TABLE = NamedNode("http://www.w3.org/ns/r2rml#logicalTable")
RR_SUBJECT_MAP = NamedNode("http://www.w3.org/ns/r2rml#subjectMap")
RML_OLD_REFERENCE = NamedNode("http://semweb.mmlab.be/ns/rml#reference")
RML_OLD_LOGICAL_SOURCE = NamedNode("http://semweb.mmlab.be/ns/rml#logicalSource")


def _term_value(term: RdfTerm) -> str:
    assert isinstance(term, (NamedNode, BlankNode, Literal))
    return term.value


def parse_mapping(mapping_content: str) -> Store:
    store = Store()
    store.load(input=io.BytesIO(mapping_content.encode("utf-8")), format=RdfFormat.TURTLE)
    return store


def extract_columns_from_mapping(mapping_content: str) -> set[str]:
    store = parse_mapping(mapping_content)
    columns: set[str] = set()
    for quad in store.quads_for_pattern(None, RR_COLUMN, None):
        columns.add(_term_value(quad.object).strip('"'))
    for quad in store.quads_for_pattern(None, RML_OLD_REFERENCE, None):
        columns.add(_term_value(quad.object).strip('"'))
    for quad in store.quads_for_pattern(None, RR_TEMPLATE, None):
        column_refs = TEMPLATE_COLUMN_REGEX.findall(_term_value(quad.object))
        columns.update(column_refs)
    for quad in store.quads_for_pattern(None, RR_CHILD, None):
        columns.add(_term_value(quad.object).strip('"'))
    for quad in store.quads_for_pattern(None, RR_PARENT, None):
        columns.add(_term_value(quad.object).strip('"'))
    return columns


def check_mapping_column_coverage(
    mapping_content: str, source_content: dict[str, dict[str, list[str]]],
) -> list[str]:
    mapped_columns = extract_columns_from_mapping(mapping_content)
    invertibility_issues = []
    for table_name, table_data in source_content.items():
        table_columns = set(table_data['columns'])
        missing_columns = table_columns - mapped_columns
        if missing_columns:
            missing_str = ", ".join(sorted(missing_columns))
            invertibility_issues.append(f"Table '{table_name}' has unmapped columns: {missing_str}")
    return invertibility_issues


def get_mapped_table_names(mapping_store: Store) -> set[str]:
    tables: set[str] = set()
    for quad in mapping_store.quads_for_pattern(None, RR_TABLE_NAME, None):
        tables.add(_term_value(quad.object).strip('"'))
    return tables


def _first_object(store: Store, subject: RdfSubject, predicate: NamedNode) -> RdfTerm | None:
    for quad in store.quads_for_pattern(subject, predicate, None):
        return quad.object
    return None


def _subjects_of(store: Store, predicate: NamedNode, obj: RdfTerm) -> list[RdfSubject]:
    return [quad.subject for quad in store.quads_for_pattern(None, predicate, obj)]


def find_subject_map_for_table(mapping_store: Store, table_name: str) -> RdfSubject | None:
    for quad in mapping_store.quads_for_pattern(None, RR_TABLE_NAME, None):
        if _term_value(quad.object).strip('"') != table_name:
            continue
        logical_table = quad.subject
        triples_maps = _subjects_of(mapping_store, RR_LOGICAL_TABLE, logical_table)
        if not triples_maps:
            triples_maps = _subjects_of(mapping_store, RML_OLD_LOGICAL_SOURCE, logical_table)
        if not triples_maps:
            continue
        result = _first_object(mapping_store, triples_maps[0], RR_SUBJECT_MAP)
        if result is not None and isinstance(result, (NamedNode, BlankNode)):
            return result
    return None


def check_null_in_subject_template(
    mapping_store: Store, source_df: pd.DataFrame, table_name: str,
) -> tuple[str | None, bool]:
    subject_map = find_subject_map_for_table(mapping_store, table_name)
    if subject_map is None:
        return None, False
    template_quad = next(mapping_store.quads_for_pattern(subject_map, RR_TEMPLATE, None), None)
    if template_quad is None:
        return None, False
    column_refs = TEMPLATE_COLUMN_REGEX.findall(_term_value(template_quad.object))
    for col in column_refs:
        if col in source_df.columns and bool(source_df[col].isna().any()):
            null_count = int(source_df[col].isna().sum())
            return (
                f"{table_name} (NON-INVERTIBLE: NULL values in subject template column "
                f"'{col}' cause {null_count} row(s) to be excluded from RDF)",
                True,
            )
    return None, False


def detect_non_invertible(
    mapping_store: Store, source_df: pd.DataFrame, table_name: str,
) -> tuple[str | None, bool]:
    null_msg, is_null = check_null_in_subject_template(mapping_store, source_df, table_name)
    if is_null:
        return null_msg, True
    return None, False


def analyze_duplicate_loss(
    source_df: pd.DataFrame, dest_df: pd.DataFrame, table_name: str,
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
            duplicate_info = "; ".join([
                f"Row {row} appears {src_cnt} times in source but {dst_cnt} times in destination"
                for src_cnt, dst_cnt, row in duplicate_rows
            ])
            message = (
                f"{table_name} (NON-INVERTIBLE: Duplicate rows lost during inversion - {duplicate_info}. "
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
    has_invertibility_issues = False

    if missing_from_dest:
        if mapping_graph:
            mapped_tables = get_mapped_table_names(mapping_graph)
            unmapped_tables = {t for t in missing_from_dest if t not in mapped_tables}
            if unmapped_tables == missing_from_dest:
                unmapped_str = ", ".join(sorted(unmapped_tables))
                mismatched_tables.append(f"NON-INVERTIBLE: Unmapped tables: {unmapped_str}")
                has_invertibility_issues = True
            else:
                return False, "Tables in source and destination databases do not match", None
        else:
            return False, "Tables in source and destination databases do not match", None

    common_tables = source_tables & dest_tables
    for table_name in common_tables:
        source_table = source_content[table_name]
        dest_table = dest_content[table_name]

        if set(source_table['columns']) != set(dest_table['columns']):
            mismatched_tables.append(f"{table_name} (columns mismatch)")
            continue

        source_df = pd.DataFrame(source_table['data'], columns=pd.Index(source_table['columns']))
        dest_df = pd.DataFrame(dest_table['data'], columns=pd.Index(dest_table['columns']))

        if source_df.empty and dest_df.empty:
            continue

        source_df = source_df.dropna(how='all')
        dest_df = dest_df.dropna(how='all')
        source_df = source_df.reindex(sorted(source_df.columns), axis=1)
        dest_df = dest_df.reindex(sorted(dest_df.columns), axis=1)
        source_df = source_df.reset_index(drop=True)
        dest_df = dest_df.reset_index(drop=True)
        source_df = source_df.sort_values(by=source_df.columns.tolist()).reset_index(drop=True)
        dest_df = dest_df.sort_values(by=dest_df.columns.tolist()).reset_index(drop=True)

        if not source_df.equals(dest_df):
            resolved = False
            if len(source_df) > len(dest_df):
                duplicate_analysis, is_dup_issue = analyze_duplicate_loss(source_df, dest_df, table_name)
                if duplicate_analysis:
                    mismatched_tables.append(duplicate_analysis)
                    if is_dup_issue:
                        has_invertibility_issues = True
                    resolved = True

            if not resolved and mapping_graph:
                issue_msg, is_issue = detect_non_invertible(mapping_graph, source_df, table_name)
                if is_issue:
                    mismatched_tables.append(issue_msg)
                    has_invertibility_issues = True
                    resolved = True

            if not resolved:
                mismatched_tables.append(f"{table_name} (data mismatch)")

    if mismatched_tables:
        message = f"Mismatched tables: {', '.join(mismatched_tables)}"

        if mapping_content and not has_invertibility_issues:
            invertibility_issues = check_mapping_column_coverage(mapping_content, source_content)
            if invertibility_issues:
                invertibility_message = "; ".join(invertibility_issues)
                message += f" (NON-INVERTIBLE: {invertibility_message})"
                has_invertibility_issues = True

        if has_invertibility_issues:
            return False, message, "non_invertible"
        return False, message, None

    return True, "All tables in source and destination databases are identical", None
