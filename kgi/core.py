# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import configparser
import logging
import os
import pathlib
import re
import tempfile
from typing import Literal as TypeLiteral
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
    RML_IRI,
    RML_OLD_QUERY,
    RML_QUERY,
    RML_REFERENCE,
    RML_REFERENCE_FORMULATION,
    RML_SOURCE,
    RML_SQL2008_QUERY,
    RML_TABLE_NAME,
    RML_TEMPLATE,
    RR_LITERAL,
    RR_SQL_QUERY,
    RR_SUBJECT_MAP,
    RR_TERM_TYPE,
    RR_TRIPLES_MAP,
)
from kgi.base import Endpoint
from kgi.endpoints import (
    EndpointFactory,
    QLeverEndpoint,
)
from kgi.exceptions import (
    MappingError,
    NoDataError,
    NonInvertibleError,
    UnsupportedMappingError,
)
from kgi.models import ReconstructedTable
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
)


SparqlBackend = TypeLiteral["pyoxigraph", "qlever"]
DEFAULT_QLEVER_ENDPOINT = "http://localhost:7019"


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
    if not dsn_quads:
        return None
    jdbc_dsn = _literal_value(dsn_quads[0])
    match = re.match(r"jdbc:(\w+)://(.+)", jdbc_dsn)
    if not match:
        return None
    db_type, host_and_db = match.group(1), match.group(2)
    driver = JDBC_DRIVERS.get(db_type)
    if not driver:
        get_logger().warning(f"Unsupported JDBC driver type: {db_type}")
        return None
    user_quads = list(store.quads_for_pattern(db_node, D2RQ_USERNAME, None))
    pass_quads = list(store.quads_for_pattern(db_node, D2RQ_PASSWORD, None))
    username = _literal_value(user_quads[0]) if user_quads else ""
    password = _literal_value(pass_quads[0]) if pass_quads else ""
    credentials = f"{quote_plus(username)}:{quote_plus(password)}@" if username else ""
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


def _generate_template(
    source_rules: pd.DataFrame, db_url: str | None = None
) -> RDBTemplate:
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


def _check_for_column_iri_term_maps(mappings: pd.DataFrame) -> bool:
    for _, rule in mappings.iterrows():
        if _is_column_only_iri(
            rule["subject_map_type"],
            rule["subject_map_value"],
            rule["subject_termtype"],
        ):
            return True
        if _is_column_only_iri(
            rule["object_map_type"], rule["object_map_value"], rule["object_termtype"]
        ):
            return True
        if _is_column_only_iri(
            rule["predicate_map_type"], rule["predicate_map_value"], RML_IRI
        ):
            return True
    return False


def _check_for_constant_only_mappings(mappings: pd.DataFrame) -> bool:
    try:
        for _, rule in mappings.iterrows():
            subject_map_type = rule.get("subject_map_type")
            predicate_map_type = rule.get("predicate_map_type")
            object_map_type = rule.get("object_map_type")

            if (
                subject_map_type in [RML_REFERENCE, RML_TEMPLATE]
                or predicate_map_type in [RML_REFERENCE, RML_TEMPLATE]
                or object_map_type in [RML_REFERENCE, RML_TEMPLATE]
            ):
                return False

        return True
    except Exception as e:
        get_logger().warning(f"Could not check for constant-only mappings: {e}")
        return False


def _reference_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value}


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


def _check_for_ambiguous_subject_templates(mappings: pd.DataFrame) -> None:
    for table_name, source_rules in mappings.groupby("logical_source_value"):
        observed_refs: set[str] = set()
        for _, rule in source_rules.iterrows():
            observed_refs.update(_reference_set(rule["predicate_references"]))
            observed_refs.update(_reference_set(rule["object_references"]))
            observed_refs.update(_reference_set(rule["graph_references"]))

        subject_infos: list[tuple[str, frozenset[tuple[str, ...]], set[str]]] = []
        for _, subject_rules in source_rules.groupby("subject_map_value", dropna=False):
            first_rule = subject_rules.iloc[0]
            if first_rule["subject_map_type"] != RML_TEMPLATE:
                continue
            subject_refs = _reference_set(first_rule["subject_references"])
            subject_infos.append(
                (
                    _signature_value(first_rule["subject_references_template"]),
                    _subject_signature(subject_rules),
                    subject_refs - observed_refs,
                )
            )

        buckets: dict[tuple[str, frozenset[tuple[str, ...]]], list[set[str]]] = {}
        for template, signature, subject_only_refs in subject_infos:
            buckets.setdefault((template, signature), []).append(subject_only_refs)

        for subject_only_refs in buckets.values():
            if len(subject_only_refs) <= 1:
                continue
            ambiguous_refs = sorted(set().union(*subject_only_refs))
            if ambiguous_refs:
                columns = ", ".join(ambiguous_refs)
                raise NonInvertibleError(
                    f"Subject templates for table '{table_name}' contain columns "
                    f"that are not observable outside indistinguishable subjects: {columns}"
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
    if source_db_url:
        data_source["db_url"] = source_db_url
    config["DataSource1"] = data_source

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".ini", delete=False, prefix="kgi_"
    )
    config.write(tmp)
    tmp.close()
    return tmp.name


def reconstruct(
    mapping: str | pathlib.Path,
    rdf_graph: str | pathlib.Path,
    source_db_url: str | None = None,
    dest_db_url: str | None = None,
    sparql_endpoint: str | None = None,
    backend: SparqlBackend = "pyoxigraph",
) -> list[ReconstructedTable]:
    logger = get_logger()
    mapping_path = str(mapping)
    rdf_graph_str = str(rdf_graph)

    mapping_store = _parse_mapping_store(mapping_path)

    if _check_for_literal_subjects(mapping_store):
        raise MappingError("rr:termType rr:Literal on subjectMap is not valid")

    if _check_for_sql_queries(mapping_store):
        raise UnsupportedMappingError("SQL query as logical table is not supported")

    if _check_for_multiple_subject_maps(mapping_store):
        raise MappingError("TriplesMap contains multiple subjectMaps")

    if not source_db_url:
        extracted_url = _extract_db_url_from_mapping(mapping_store)
        if extracted_url:
            source_db_url = extracted_url
            logger.info(f"Extracted source database URL from mapping: {extracted_url}")

    config_file = _build_morph_config(mapping, rdf_graph_str, source_db_url)
    endpoint: Endpoint | None = None
    schema_retrievers: dict[str, DatabaseSchemaRetriever] = {}
    try:
        config = load_config_from_argument(config_file)

        try:
            mappings, _, _ = retrieve_mappings(config)
        except ValueError as e:
            raise MappingError(f"Invalid mapping: {e}") from e
        except KeyError as e:
            if str(e) == "'object_map'":
                raise MappingError("Mapping with missing object_map information") from e
            raise MappingError(f"Mapping error: {e}") from e

        _normalize_sql_table_sources(mappings)

        if _check_for_constant_only_mappings(mappings):
            raise NonInvertibleError(
                "Mappings contain only constants (no column references) - original data cannot be recovered"
            )

        if _check_for_column_iri_term_maps(mappings):
            raise NonInvertibleError(
                "Term map uses rr:column with IRI term type - base IRI resolution makes inversion ambiguous"
            )

        insert_columns(mappings)
        mappings = mappings[mappings["logical_source_type"] != RML_SOURCE]
        _check_for_ambiguous_subject_templates(mappings)

        if source_db_url:
            schema_retrievers["DataSource1"] = DatabaseSchemaRetriever(source_db_url)

        if backend not in ("pyoxigraph", "qlever"):
            raise ValueError(f"Unsupported SPARQL backend: {backend}")

        try:
            if backend == "pyoxigraph":
                endpoint = EndpointFactory.create_from_url(rdf_graph_str)
            else:
                endpoint = QLeverEndpoint(
                    sparql_endpoint or DEFAULT_QLEVER_ENDPOINT,
                    rdf_file_to_load=rdf_graph_str,
                )
        except (FileNotFoundError, OSError) as e:
            raise NoDataError(
                "No RDF input file found, likely due to mapping errors"
            ) from e
        except ValueError as e:
            raise NonInvertibleError(f"Output RDF contains invalid data: {e}") from e

        results: list[ReconstructedTable] = []
        for table_name_value, source_rules in mappings.groupby("logical_source_value"):
            table_name = str(table_name_value)
            source_section = source_rules.iloc[0].get("source_section", "DataSource1")
            template_db_url = dest_db_url if dest_db_url else source_db_url
            template = _generate_template(source_rules, template_db_url)

            source_data, _ = retrieve_data(
                mappings, source_rules, endpoint, decode_columns=True
            )

            if source_data is None:
                results.append(ReconstructedTable(name=table_name, data=pd.DataFrame()))
                logger.warning(f"No data generated for {table_name}")
                continue

            if source_section in schema_retrievers:
                schema_retriever = schema_retrievers[source_section]
                table_schema = schema_retriever.get_table_schema(table_name)
                if table_schema:
                    source_data = apply_schema_types(source_data, table_schema)
                    source_data = apply_schema_ordering(source_data, table_schema)

            template.fill_data(source_data, table_name)
            results.append(ReconstructedTable(name=table_name, data=source_data))

        if not results:
            raise NoDataError("No data was generated during reconstruction")

        return results
    finally:
        if endpoint is not None:
            endpoint.close()
        for retriever in schema_retrievers.values():
            retriever.dispose()
        os.unlink(config_file)
