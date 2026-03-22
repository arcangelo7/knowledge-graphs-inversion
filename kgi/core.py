# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import configparser
import logging
import os
import pathlib
import re
import tempfile
from urllib.parse import quote_plus

import pandas as pd
from morph_kgc.args_parser import load_config_from_argument
from morph_kgc.mapping.mapping_parser import retrieve_mappings
from pyoxigraph import BlankNode, Literal, NamedNode, Quad, RdfFormat, Store

from .constants import (
    RML_IRI,
    RML_REFERENCE,
    RML_SOURCE,
    RML_TEMPLATE,
    RR_LITERAL,
    RR_SUBJECT_MAP,
    RR_TERM_TYPE,
)
from .endpoints import EndpointFactory, RemoteEndpoint, VirtuosoEndpoint
from .exceptions import (
    MappingError,
    NoDataError,
    NonInvertibleError,
    UnsupportedMappingError,
)
from .models import ReconstructedTable
from .query import retrieve_data
from .schema import DatabaseSchemaRetriever, apply_schema_ordering, apply_schema_types
from .templates import RDBTemplate
from .utils import insert_columns

D2RQ_DATABASE = NamedNode("http://www.wiwiss.fu-berlin.de/suhl/bizer/D2RQ/0.1#Database")
D2RQ_JDBC_DSN = NamedNode("http://www.wiwiss.fu-berlin.de/suhl/bizer/D2RQ/0.1#jdbcDSN")
D2RQ_USERNAME = NamedNode("http://www.wiwiss.fu-berlin.de/suhl/bizer/D2RQ/0.1#username")
D2RQ_PASSWORD = NamedNode("http://www.wiwiss.fu-berlin.de/suhl/bizer/D2RQ/0.1#password")

JDBC_DRIVERS: dict[str, str] = {
    "postgresql": "postgresql+psycopg2",
    "mysql": "mysql+pymysql",
}

RR_SQL_QUERY = NamedNode("http://www.w3.org/ns/r2rml#sqlQuery")
RML_QUERY_NEW = NamedNode("http://w3id.org/rml/query")
RML_QUERY_LEGACY = NamedNode("http://semweb.mmlab.be/ns/rml#query")
RR_TRIPLES_MAP = NamedNode("http://www.w3.org/ns/r2rml#TriplesMap")
RDF_TYPE = NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")


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
    for predicate in (RR_SQL_QUERY, RML_QUERY_NEW, RML_QUERY_LEGACY):
        if any(store.quads_for_pattern(None, predicate, None)):
            return True
    return False


def _check_for_multiple_subject_maps(store: Store) -> bool:
    for quad in store.quads_for_pattern(None, RDF_TYPE, RR_TRIPLES_MAP):
        triples_map = quad.subject
        subject_maps = list(
            store.quads_for_pattern(triples_map, RR_SUBJECT_MAP, None)
        )
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
        if stripped.startswith("{") and stripped.endswith("}") and stripped.count("{") == 1:
            return True
    return False


def _check_for_column_iri_term_maps(mappings: pd.DataFrame) -> bool:
    for _, rule in mappings.iterrows():
        if _is_column_only_iri(rule["subject_map_type"], rule["subject_map_value"], rule["subject_termtype"]):
            return True
        if _is_column_only_iri(rule["object_map_type"], rule["object_map_value"], rule["object_termtype"]):
            return True
        if _is_column_only_iri(rule["predicate_map_type"], rule["predicate_map_value"], RML_IRI):
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
    use_virtuoso: bool = False,
    virtuoso_container: str = "virtuoso-kgi",
) -> dict[str, ReconstructedTable]:
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
    try:
        config = load_config_from_argument(config_file)

        try:
            mappings, _, _ = retrieve_mappings(config)
        except ValueError as e:
            raise MappingError(f"Invalid mapping: {e}") from e
        except KeyError as e:
            if str(e) == "'object_map'":
                raise MappingError(
                    "Mapping with missing object_map information"
                ) from e
            raise MappingError(f"Mapping error: {e}") from e

        if _check_for_constant_only_mappings(mappings):
            raise NonInvertibleError(
                "Mappings contain only constants (no column references) - original data cannot be recovered"
            )

        if _check_for_column_iri_term_maps(mappings):
            raise NonInvertibleError(
                "Term map uses rr:column with IRI term type - base IRI resolution makes inversion ambiguous"
            )

        try:
            if sparql_endpoint:
                if use_virtuoso:
                    endpoint = VirtuosoEndpoint(
                        sparql_endpoint,
                        rdf_file_to_load=rdf_graph_str,
                        container_name=virtuoso_container,
                    )
                else:
                    endpoint = RemoteEndpoint(
                        sparql_endpoint, rdf_file_to_load=rdf_graph_str
                    )
            else:
                endpoint = EndpointFactory.create_from_url(rdf_graph_str)
        except (FileNotFoundError, OSError) as e:
            raise NoDataError(
                "No RDF input file found, likely due to mapping errors"
            ) from e
        except ValueError as e:
            raise NonInvertibleError(
                f"Output RDF contains invalid data: {e}"
            ) from e

        insert_columns(mappings)

        schema_retrievers: dict[str, DatabaseSchemaRetriever] = {}
        if source_db_url:
            schema_retrievers["DataSource1"] = DatabaseSchemaRetriever(source_db_url)

        mappings = mappings[mappings["logical_source_type"] != RML_SOURCE]

        results: dict[str, ReconstructedTable] = {}
        for table_name, source_rules in mappings.groupby("logical_source_value"):
            source_section = source_rules.iloc[0].get(
                "source_section", "DataSource1"
            )
            template_db_url = dest_db_url if dest_db_url else source_db_url
            template = _generate_template(source_rules, template_db_url)

            source_data, sparql_query = retrieve_data(
                mappings, source_rules, endpoint, decode_columns=True
            )

            if source_data is None:
                results[table_name] = ReconstructedTable(
                    sql="", sparql_query="", data=pd.DataFrame()
                )
                logger.warning(f"No data generated for {table_name}")
                continue

            if source_section in schema_retrievers:
                schema_retriever = schema_retrievers[source_section]
                table_schema = schema_retriever.get_table_schema(table_name)
                if table_schema:
                    source_data = apply_schema_types(source_data, table_schema)
                    source_data = apply_schema_ordering(source_data, table_schema)

            filled_source = template.fill_data(source_data, table_name)
            results[table_name] = ReconstructedTable(
                sql=filled_source,
                sparql_query=sparql_query or "",
                data=source_data,
            )

        for retriever in schema_retrievers.values():
            retriever.dispose()

        if not results:
            raise NoDataError("No data was generated during reconstruction")

        return results
    finally:
        os.unlink(config_file)
