# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import configparser
import logging
import os
import pathlib
import re
import tempfile
from dataclasses import dataclass
from typing import cast
from urllib.parse import quote_plus

import pandas as pd
from morph_kgc.args_parser import load_config_from_argument
from morph_kgc.mapping.mapping_parser import retrieve_mappings
from pyoxigraph import BlankNode, Literal, NamedNode, Quad, RdfFormat, Store

from kgi.constants import (
    D2RQ_DATABASE,
    D2RQ_JDBC_DSN,
    D2RQ_PASSWORD,
    D2RQ_USERNAME,
    JDBC_DRIVERS,
    RDF_TYPE,
    REF_TEMPLATE_REGEX,
    RML_CHILD,
    RML_IRI,
    RML_ITERATOR,
    RML_LOGICAL_SOURCE,
    RML_OLD_LOGICAL_SOURCE,
    RML_OLD_QUERY,
    RML_OLD_REFERENCE,
    RML_PARENT_TRIPLES_MAP,
    RML_QUERY,
    RML_REFERENCE,
    RML_REFERENCE_FORMULATION,
    RML_REFERENCE_NODE,
    RML_SOURCE,
    RML_SQL2008_QUERY,
    RML_TABLE_NAME,
    RML_TEMPLATE,
    RML_TEMPLATE_NODE,
    RR_CHILD,
    RR_COLUMN,
    RR_LITERAL,
    RR_LOGICAL_TABLE,
    RR_PARENT_TRIPLES_MAP,
    RR_SQL_QUERY,
    RR_SUBJECT_MAP,
    RR_TABLE_NAME,
    RR_TEMPLATE,
    RR_TERM_TYPE,
    RR_TRIPLES_MAP,
)
from kgi.base import Endpoint
from kgi.endpoints import EndpointFactory
from kgi.exceptions import (
    MappingError,
    NoDataError,
    NonInvertibleError,
    UnsupportedMappingError,
)
from kgi.query import retrieve_data
from kgi.schema import (
    DatabaseSchemaRetriever,
    apply_schema_ordering,
    apply_schema_types,
)
from kgi.templates import RDBTemplate
from kgi.utils import (
    insert_columns,
    normalize_sql_identifier,
    signature_value as _signature_value,
    undelimited_sql_identifier,
)


def get_logger() -> logging.Logger:
    return logging.getLogger("kgi")


def _parse_mapping_store(mapping_file: str) -> Store:
    store = Store()
    store.load(path=mapping_file, format=RdfFormat.TURTLE)
    return store


def _literal_value(quad: Quad) -> str:
    obj = quad.object
    if isinstance(obj, Literal):
        return obj.value
    return str(obj)


def _extract_db_url_from_mapping(store: Store) -> str | None:
    databases = list(store.quads_for_pattern(None, RDF_TYPE, D2RQ_DATABASE))
    if not databases:
        return None
    db_node = databases[0].subject
    dsn_quads = list(store.quads_for_pattern(db_node, D2RQ_JDBC_DSN, None))
    jdbc_dsn = _literal_value(dsn_quads[0])
    match = re.match(r"jdbc:(\w+)://(.+)", jdbc_dsn)
    if match is None:
        raise ValueError(f"Invalid JDBC DSN: {jdbc_dsn}")
    db_type, host_and_db = match.group(1), match.group(2)
    driver = JDBC_DRIVERS[db_type]
    user_quads = list(store.quads_for_pattern(db_node, D2RQ_USERNAME, None))
    pass_quads = list(store.quads_for_pattern(db_node, D2RQ_PASSWORD, None))
    username = _literal_value(user_quads[0])
    password = _literal_value(pass_quads[0])
    credentials = f"{quote_plus(username)}:{quote_plus(password)}@"
    return f"{driver}://{credentials}{host_and_db}"


def _check_for_sql_queries(store: Store) -> bool:
    for predicate in (RR_SQL_QUERY, RML_QUERY, RML_OLD_QUERY):
        if any(store.quads_for_pattern(None, predicate, None)):
            return True
    return any(
        store.quads_for_pattern(None, RML_REFERENCE_FORMULATION, RML_SQL2008_QUERY)
    )


def _normalize_sql_table_sources(mappings: pd.DataFrame) -> None:
    # New-vocabulary mappings carry the table name in rml:iterator; morph-kgc
    # leaves the rule as a generic rml:source instead of the tableName form
    # the rest of the pipeline expects
    is_table_source = (
        (mappings["source_type"] == "RDB")
        & (mappings["logical_source_type"] == RML_SOURCE)
        & mappings["iterator"].notna()
    )
    if not is_table_source.any():
        return
    mappings.loc[is_table_source, "logical_source_value"] = mappings.loc[
        is_table_source, "iterator"
    ].map(normalize_sql_identifier)
    mappings.loc[is_table_source, "logical_source_type"] = RML_TABLE_NAME


def _check_for_multiple_subject_maps(store: Store) -> bool:
    for quad in store.quads_for_pattern(None, RDF_TYPE, RR_TRIPLES_MAP):
        triples_map = quad.subject
        subject_maps = list(store.quads_for_pattern(triples_map, RR_SUBJECT_MAP, None))
        if len(subject_maps) > 1:
            return True
    return False


def _check_for_literal_subjects(store: Store) -> bool:
    for sm_quad in store.quads_for_pattern(None, RR_SUBJECT_MAP, None):
        subject_map_node = sm_quad.object
        if not isinstance(subject_map_node, (NamedNode, BlankNode)):
            continue
        if any(store.quads_for_pattern(subject_map_node, RR_TERM_TYPE, RR_LITERAL)):
            return True
    return False


_TABLE_NAME_PREDICATES = (RR_TABLE_NAME, RML_ITERATOR)
# A referencing object map reads columns of the parent triples map's own table
_PARENT_TRIPLES_MAP_PREDICATES = (
    RR_PARENT_TRIPLES_MAP,
    NamedNode(RML_PARENT_TRIPLES_MAP),
)


@dataclass(frozen=True)
class _Vocabulary:
    """How a mapping language names the columns it reads.

    R2RML writes SQL identifiers, so the database folds a reference that carries
    no delimiters. RML-Core names the column of the result set itself, and its
    test cases read a delimited column through an undelimited reference.
    """

    logical_sources: tuple[NamedNode, ...]
    template: NamedNode
    references: tuple[NamedNode, ...]
    folds_regular_identifiers: bool

    def resolve(self, reference: str) -> str:
        if self.folds_regular_identifiers:
            return normalize_sql_identifier(reference)
        return undelimited_sql_identifier(reference)


_VOCABULARIES = (
    _Vocabulary(
        (RR_LOGICAL_TABLE,),
        RR_TEMPLATE,
        (RR_COLUMN, RR_CHILD),
        folds_regular_identifiers=True,
    ),
    _Vocabulary(
        (RML_LOGICAL_SOURCE, RML_OLD_LOGICAL_SOURCE),
        RML_TEMPLATE_NODE,
        (RML_REFERENCE_NODE, RML_OLD_REFERENCE, RML_CHILD),
        folds_regular_identifiers=False,
    ),
)


def _logical_table_name(
    store: Store, logical_source: NamedNode | BlankNode
) -> str | None:
    """Resolve the table the logical source names.

    Both languages write a SQL identifier here, so both fold it, while only
    R2RML folds the references that name the columns.
    """
    for predicate in _TABLE_NAME_PREDICATES:
        for quad in store.quads_for_pattern(logical_source, predicate, None):
            if isinstance(quad.object, Literal):
                return normalize_sql_identifier(quad.object.value)
    return None


def _triples_map_references(
    store: Store, triples_map: NamedNode | BlankNode, vocabulary: _Vocabulary
) -> set[str]:
    references: set[str] = set()
    visited: set[NamedNode | BlankNode] = {triples_map}
    pending = [triples_map]
    while pending:
        node = pending.pop()
        for quad in store.quads_for_pattern(node, None, None):
            if quad.predicate in _PARENT_TRIPLES_MAP_PREDICATES:
                continue
            value = quad.object
            if isinstance(value, Literal):
                if quad.predicate == vocabulary.template:
                    references.update(
                        vocabulary.resolve(reference)
                        for reference in re.findall(REF_TEMPLATE_REGEX, value.value)
                    )
                elif quad.predicate in vocabulary.references:
                    references.add(vocabulary.resolve(value.value))
            elif isinstance(value, (NamedNode, BlankNode)) and value not in visited:
                visited.add(value)
                pending.append(value)
    return references


def mapped_references(store: Store) -> dict[str, set[str]]:
    """Source columns the mapping reads, per logical table.

    The mapping is read as written, because morph-kgc drops the delimiters that
    tell a reference how the database resolves it.
    """
    references: dict[str, set[str]] = {}
    for vocabulary in _VOCABULARIES:
        for predicate in vocabulary.logical_sources:
            for quad in store.quads_for_pattern(None, predicate, None):
                if not isinstance(
                    quad.subject, (NamedNode, BlankNode)
                ) or not isinstance(quad.object, (NamedNode, BlankNode)):
                    continue
                table_name = _logical_table_name(store, quad.object)
                if table_name is None:
                    continue
                references.setdefault(table_name, set()).update(
                    _triples_map_references(store, quad.subject, vocabulary)
                )
    return references


def _check_for_missing_references(store: Store, source_db_url: str) -> None:
    """Reject a mapping that reads a column the source table does not have."""
    retriever = DatabaseSchemaRetriever(source_db_url)
    try:
        ignores_case = retriever.engine.dialect.name == "mysql"
        for table_name, references in mapped_references(store).items():
            stored = {
                column.name for column in retriever.get_table_schema(table_name).columns
            }
            if ignores_case:
                stored = {name.lower() for name in stored}
                references = {reference.lower() for reference in references}
            missing = sorted(references - stored)
            if missing:
                raise MappingError(
                    f"Table '{table_name}' of the source database has no column "
                    f"named {', '.join(missing)}"
                )
    finally:
        retriever.dispose()


def _generate_template(source_rules: pd.DataFrame, db_url: str) -> RDBTemplate:
    source_type = source_rules.iloc[0]["source_type"]

    if source_type == "RDB":
        return RDBTemplate(db_url)
    else:
        raise ValueError(f"Unsupported source type: {source_type}")


def _is_column_only_iri(map_type: object, map_value: object, term_type: object) -> bool:
    if map_type == RML_REFERENCE and term_type == RML_IRI:
        return True
    if map_type == RML_TEMPLATE and term_type == RML_IRI:
        stripped = str(map_value).strip()
        if (
            stripped.startswith("{")
            and stripped.endswith("}")
            and stripped.count("{") == 1
        ):
            return True
    return False


def _term_map_references(
    rule: pd.Series, join_targets: set[str]
) -> tuple[set[str], set[str]]:
    """Split the rule's references into the opaque ones and the exposed ones.

    A term map that builds an IRI out of a bare column reference names a column
    without exposing it, because the base IRI it resolves against cannot be
    separated from the column value. A triples map with no predicate-object map
    generates no triples, so the graph carries its subjects only through a join
    that references it, and exposes nothing without one.
    """
    if (
        cast(bool, pd.isna(rule["object_map_type"]))
        and rule["triples_map_id"] not in join_targets
    ):
        return _reference_set(rule["subject_references"]), set()

    opaque: set[str] = set()
    exposed: set[str] = set()
    for references, map_type, map_value, term_type in (
        (
            rule["subject_references"],
            rule["subject_map_type"],
            rule["subject_map_value"],
            rule["subject_termtype"],
        ),
        (
            rule["predicate_references"],
            rule["predicate_map_type"],
            rule["predicate_map_value"],
            RML_IRI,
        ),
        (
            rule["object_references"],
            rule["object_map_type"],
            rule["object_map_value"],
            rule["object_termtype"],
        ),
    ):
        target = (
            opaque if _is_column_only_iri(map_type, map_value, term_type) else exposed
        )
        target.update(_reference_set(references))
    exposed.update(_reference_set(rule["graph_references"]))
    return opaque, exposed


def _check_for_constant_only_mappings(mappings: pd.DataFrame) -> bool:
    for _, rule in mappings.iterrows():
        subject_map_type = rule["subject_map_type"]
        predicate_map_type = rule["predicate_map_type"]
        object_map_type = rule["object_map_type"]

        if (
            subject_map_type in [RML_REFERENCE, RML_TEMPLATE]
            or predicate_map_type in [RML_REFERENCE, RML_TEMPLATE]
            or object_map_type in [RML_REFERENCE, RML_TEMPLATE]
        ):
            return False

    return True


def _reference_set(value: object) -> set[str]:
    return {str(item) for item in cast(list[object], value)}


def _subject_signature(subject_rules: pd.DataFrame) -> frozenset[tuple[str, ...]]:
    signature_columns = [
        "predicate_map_type",
        "predicate_map_value",
        "object_map_type",
        "object_map_value",
        "object_termtype",
        "graph_map_type",
        "graph_map_value",
    ]
    signature = []
    for _, rule in subject_rules.iterrows():
        signature.append(
            tuple(_signature_value(rule[column]) for column in signature_columns)
        )
    return frozenset(signature)


def _all_references(rule: pd.Series) -> set[str]:
    return (
        _reference_set(rule["subject_references"])
        | _reference_set(rule["predicate_references"])
        | _reference_set(rule["object_references"])
        | _reference_set(rule["graph_references"])
    )


def _ambiguous_subject_references(source_rules: pd.DataFrame) -> set[str]:
    """Columns reachable only through subject templates that cannot be told apart.

    Subject maps sharing a template skeleton and a predicate-object signature build
    interchangeable IRIs, so a value found in one of them could belong to any of the
    columns those templates reference.
    """
    observed_refs: set[str] = set()
    for _, rule in source_rules.iterrows():
        observed_refs.update(_reference_set(rule["predicate_references"]))
        observed_refs.update(_reference_set(rule["object_references"]))
        observed_refs.update(_reference_set(rule["graph_references"]))

    buckets: dict[tuple[str, frozenset[tuple[str, ...]]], list[set[str]]] = {}
    for _, subject_rules in source_rules.groupby("subject_map_value", dropna=False):
        first_rule = subject_rules.iloc[0]
        if first_rule["subject_map_type"] != RML_TEMPLATE:
            continue
        subject_refs = _reference_set(first_rule["subject_references"])
        key = (
            _signature_value(first_rule["subject_references_template"]),
            _subject_signature(subject_rules),
        )
        buckets.setdefault(key, []).append(subject_refs - observed_refs)

    ambiguous: set[str] = set()
    for subject_only_refs in buckets.values():
        if len(subject_only_refs) > 1:
            ambiguous.update(*subject_only_refs)
    return ambiguous


def _ambiguous_graph_references(source_rules: pd.DataFrame) -> set[str]:
    """Columns reachable only through graph maps that cannot be told apart.

    Graph maps sharing a template skeleton produce graph IRIs that cannot be traced
    back to the term map that built them.
    """
    observable_refs: set[str] = set()
    for _, rule in source_rules.iterrows():
        observable_refs.update(_reference_set(rule["subject_references"]))
        observable_refs.update(_reference_set(rule["predicate_references"]))
        observable_refs.update(_reference_set(rule["object_references"]))

    skeletons: dict[str, dict[str, set[str]]] = {}
    for _, rule in source_rules.iterrows():
        graph_refs = _reference_set(rule["graph_references"])
        if not graph_refs:
            continue
        skeleton = _signature_value(rule["graph_references_template"])
        graph_maps = skeletons.setdefault(skeleton, {})
        graph_maps.setdefault(_signature_value(rule["graph_map_value"]), set()).update(
            graph_refs
        )

    ambiguous: set[str] = set()
    for graph_maps in skeletons.values():
        if len(graph_maps) > 1:
            ambiguous.update(set().union(*graph_maps.values()) - observable_refs)
    return ambiguous


def _unrecoverable_references(mappings: pd.DataFrame) -> dict[str, frozenset[str]]:
    """Columns whose values the graph carries but cannot attribute to them.

    They are left out of the reconstruction: the remaining columns are recovered as
    usual, the same way columns the mapping never uses are simply absent.
    """
    join_targets = set(
        mappings.loc[
            mappings["object_map_type"] == RML_PARENT_TRIPLES_MAP, "object_map_value"
        ]
    )
    unrecoverable: dict[str, frozenset[str]] = {}
    for table_name, source_rules in mappings.groupby("logical_source_value"):
        ambiguous = _ambiguous_subject_references(source_rules)
        ambiguous.update(_ambiguous_graph_references(source_rules))

        opaque: set[str] = set()
        exposed: set[str] = set()
        for _, rule in source_rules.iterrows():
            rule_opaque, rule_exposed = _term_map_references(rule, join_targets)
            opaque.update(rule_opaque)
            exposed.update(rule_exposed)

        unrecoverable[str(table_name)] = frozenset(
            ambiguous | (opaque - (exposed - ambiguous))
        )
    return unrecoverable


@dataclass(frozen=True)
class TableAnalysis:
    """What the graph preserves of one logical table.

    `subject_reference_sets` holds one entry per distinct subject map, because a
    row reaches the graph only through a subject map whose columns are all
    non-NULL.
    """

    references: frozenset[str]
    unrecoverable: frozenset[str]
    subject_reference_sets: tuple[frozenset[str], ...]

    @property
    def recoverable(self) -> frozenset[str]:
        return self.references - self.unrecoverable


MappingAnalysis = dict[str, TableAnalysis]


def _analyze_rules(mappings: pd.DataFrame) -> MappingAnalysis:
    unrecoverable = _unrecoverable_references(mappings)
    analysis: MappingAnalysis = {}
    for table_name, source_rules in mappings.groupby("logical_source_value"):
        references: set[str] = set()
        subject_reference_sets: list[frozenset[str]] = []
        for _, rule in source_rules.iterrows():
            references.update(_all_references(rule))
            subject_reference_sets.append(
                frozenset(_reference_set(rule["subject_references"]))
            )
        analysis[str(table_name)] = TableAnalysis(
            frozenset(references),
            unrecoverable[str(table_name)],
            tuple(dict.fromkeys(subject_reference_sets)),
        )
    return analysis


def _check_for_unrecoverable_tables(analysis: MappingAnalysis) -> None:
    for table_name, table in analysis.items():
        if not table.recoverable:
            columns = ", ".join(sorted(table.references))
            raise NonInvertibleError(
                f"No column of table '{table_name}' can be recovered from the "
                f"graph: {columns}"
            )


def _build_morph_config(
    mapping: str | pathlib.Path,
    rdf_graph: str,
    source_db_url: str | None = None,
) -> str:
    config = configparser.ConfigParser()
    config["CONFIGURATION"] = {
        "output_file": str(rdf_graph),
        "output_format": "N-QUADS",
        "logging_level": "ERROR",
    }
    data_source: dict[str, str] = {"mappings": str(mapping)}
    if source_db_url is not None:
        data_source["db_url"] = source_db_url
    config["DataSource1"] = data_source

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".ini", delete=False, prefix="kgi_"
    )
    config.write(tmp)
    tmp.close()
    return tmp.name


def _load_mapping_rules(
    mapping: str | pathlib.Path,
    rdf_graph: str,
    source_db_url: str | None,
) -> tuple[pd.DataFrame, str | None]:
    logger = get_logger()
    mapping_store = _parse_mapping_store(str(mapping))

    if _check_for_literal_subjects(mapping_store):
        raise MappingError("rr:termType rr:Literal on subjectMap is not valid")

    if _check_for_sql_queries(mapping_store):
        raise UnsupportedMappingError("SQL query as logical table is not supported")

    if _check_for_multiple_subject_maps(mapping_store):
        raise MappingError("TriplesMap contains multiple subjectMaps")

    if source_db_url is None:
        extracted_url = _extract_db_url_from_mapping(mapping_store)
        if extracted_url is not None:
            source_db_url = extracted_url
            logger.info(f"Extracted source database URL from mapping: {extracted_url}")

    if source_db_url is not None:
        _check_for_missing_references(mapping_store, source_db_url)

    config_file = _build_morph_config(mapping, rdf_graph, source_db_url)
    try:
        config = load_config_from_argument(config_file)
        mappings, _, _ = retrieve_mappings(config)
    finally:
        os.unlink(config_file)

    _normalize_sql_table_sources(mappings)
    if (mappings["logical_source_type"] == RML_SOURCE).any():
        raise UnsupportedMappingError("rml:source logical sources are not supported")

    if _check_for_constant_only_mappings(mappings):
        raise NonInvertibleError(
            "Mappings contain only constants (no column references) - original data cannot be recovered"
        )

    insert_columns(mappings)
    return mappings, source_db_url


def analyze_mapping(
    mapping: str | pathlib.Path,
    rdf_graph: str | pathlib.Path,
    *,
    source_db_url: str | None = None,
) -> MappingAnalysis:
    """Report, per logical table, which columns the graph preserves.

    The inversion and the comparison of the two databases must agree on what
    can be recovered, so both read this single description of the mapping.
    """
    mappings, _ = _load_mapping_rules(mapping, str(rdf_graph), source_db_url)
    analysis = _analyze_rules(mappings)
    _check_for_unrecoverable_tables(analysis)
    return analysis


def reconstruct(
    mapping: str | pathlib.Path,
    rdf_graph: str | pathlib.Path,
    *,
    dest_db_url: str,
    source_db_url: str | None = None,
) -> None:
    logger = get_logger()
    rdf_graph_str = str(rdf_graph)

    mappings, source_db_url = _load_mapping_rules(mapping, rdf_graph_str, source_db_url)
    analysis = _analyze_rules(mappings)
    _check_for_unrecoverable_tables(analysis)

    endpoint: Endpoint | None = None
    schema_retrievers: dict[str, DatabaseSchemaRetriever] = {}
    try:
        if source_db_url is not None:
            schema_retrievers["DataSource1"] = DatabaseSchemaRetriever(source_db_url)

        endpoint = EndpointFactory.create_from_url(rdf_graph_str)

        reconstructed_table_count = 0
        for table_name_value, source_rules in mappings.groupby("logical_source_value"):
            table_name = str(table_name_value)
            source_section = str(source_rules.iloc[0]["source_name"])
            template = _generate_template(source_rules, dest_db_url)

            excluded_references = analysis[table_name].unrecoverable
            if excluded_references:
                columns = ", ".join(sorted(excluded_references))
                logger.warning(
                    f"Columns of table '{table_name}' left out of the reconstruction "
                    f"because the graph does not expose them unambiguously: {columns}"
                )

            source_data_chunks, _ = retrieve_data(
                mappings,
                source_rules,
                endpoint,
                excluded_references,
                decode_columns=True,
            )
            if source_data_chunks is None:
                raise NonInvertibleError(
                    f"No column of table '{table_name}' can be recovered from the graph"
                )
            table_schema = schema_retrievers[source_section].get_table_schema(
                table_name
            )
            source_data_chunks = (
                apply_schema_ordering(
                    apply_schema_types(chunk, table_schema), table_schema
                )
                for chunk in source_data_chunks
            )

            template.fill_data(source_data_chunks, table_name, table_schema)
            reconstructed_table_count += 1

        if reconstructed_table_count == 0:
            raise NoDataError("No data was generated during reconstruction")
    finally:
        if endpoint is not None:
            endpoint.close()
        for retriever in schema_retrievers.values():
            retriever.dispose()
