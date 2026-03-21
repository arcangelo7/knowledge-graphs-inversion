# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import configparser
import logging
import os
import pathlib
import tempfile

import pandas as pd
from morph_kgc.args_parser import load_config_from_argument
from morph_kgc.mapping.mapping_parser import retrieve_mappings
from pyoxigraph import NamedNode, RdfFormat, Store

from .constants import (
    RML_BLANK_NODE,
    RML_PARENT_TRIPLES_MAP,
    RML_REFERENCE,
    RML_TEMPLATE,
    RR_SUBJECT_MAP,
    TEST_LOG_FOLDER,
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
from .templates import CSVTemplate, JSONTemplate, RDBTemplate
from .utils import insert_columns

RR_SQL_QUERY = NamedNode("http://www.w3.org/ns/r2rml#sqlQuery")
RR_TRIPLES_MAP = NamedNode("http://www.w3.org/ns/r2rml#TriplesMap")
RDF_TYPE = NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")


def get_logger() -> logging.Logger:
    return logging.getLogger("kgi")


def _parse_mapping_store(mapping_file: str) -> Store:
    store = Store()
    store.load(path=mapping_file, format=RdfFormat.TURTLE)
    return store


def _check_for_sql_queries(mapping_path: str) -> bool:
    try:
        if os.path.exists(mapping_path):
            store = _parse_mapping_store(mapping_path)
            if any(store.quads_for_pattern(None, RR_SQL_QUERY, None)):
                return True
        return False
    except Exception as e:
        get_logger().warning(
            f"Could not parse mapping file to check for SQL queries: {e}"
        )
        return False


def _check_for_multiple_subject_maps(mapping_path: str) -> bool:
    try:
        if os.path.exists(mapping_path):
            store = _parse_mapping_store(mapping_path)
            for quad in store.quads_for_pattern(None, RDF_TYPE, RR_TRIPLES_MAP):
                triples_map = quad.subject
                subject_maps = list(
                    store.quads_for_pattern(triples_map, RR_SUBJECT_MAP, None)
                )
                if len(subject_maps) > 1:
                    return True
        return False
    except Exception as e:
        get_logger().warning(f"Could not check for multiple subject maps: {e}")
        return False


def _generate_template(
    source_rules: pd.DataFrame, db_url: str | None = None
) -> CSVTemplate | RDBTemplate | JSONTemplate:
    source_type = source_rules.iloc[0]["source_type"]

    if source_type == "JSON":
        template = JSONTemplate()
        for _, rule in source_rules.iterrows():
            if rule["object_map_type"] in [RML_BLANK_NODE, RML_PARENT_TRIPLES_MAP]:
                continue
            iterator = rule["iterator"]
            for value in (
                rule["subject_references"]
                + rule["predicate_references"]
                + rule["object_references"]
            ):
                splitted = value.split(".")
                predecessors = ".".join(splitted[:-1])
                path = f"{iterator}.{predecessors}['{splitted[-1]}']"
                template.add_path(path)
        return template
    elif source_type == "CSV":
        return CSVTemplate()
    elif source_type == "RDB":
        return RDBTemplate(db_url)
    else:
        raise ValueError(f"Unsupported source type: {source_type}")


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


def test_logging_setup(test_id: str) -> None:
    if not os.path.exists(TEST_LOG_FOLDER):
        os.mkdir(TEST_LOG_FOLDER)

    log_file = TEST_LOG_FOLDER / f"{test_id}.log"
    if os.path.exists(log_file):
        os.remove(log_file)

    logger = get_logger()
    for handler in logger.handlers[:]:
        if isinstance(handler, logging.FileHandler):
            handler.close()
            logger.removeHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    formatter = logging.Formatter("%(name)s - %(levelname)s - %(message)s")
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def logging_setup() -> None:
    if os.path.exists("inversion.log"):
        os.remove("inversion.log")

    logger = get_logger()
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(name)s - %(levelname)s - %(message)s")

    file_handler = logging.FileHandler("inversion.log")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.propagate = False


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

    if _check_for_sql_queries(mapping_path):
        raise UnsupportedMappingError("SQL query as logical table is not supported")

    if _check_for_multiple_subject_maps(mapping_path):
        raise MappingError("TriplesMap contains multiple subjectMaps")

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
