"""Core inversion functionality."""

import logging
import os
import pathlib

import morph_kgc.config
import pandas as pd
from morph_kgc.args_parser import load_config_from_argument
from morph_kgc.mapping.mapping_parser import retrieve_mappings

from .constants import RML_BLANK_NODE, RML_PARENT_TRIPLES_MAP, TEST_LOG_FOLDER
from .endpoints import EndpointFactory, RemoteEndpoint, VirtuosoEndpoint
from .query import retrieve_data
from .templates import CSVTemplate, JSONTemplate, RDBTemplate
from .utils import insert_columns


def get_logger() -> logging.Logger:
    """Get the KGI logger."""
    return logging.getLogger("kgi")


def generate_template(source_rules: pd.DataFrame, db_url: str = None):
    """Generate appropriate template based on source type."""
    source_type = source_rules.iloc[0]["source_type"]

    if source_type == "JSON":
        template = JSONTemplate()
        for _, rule in source_rules.iterrows():
            if rule["object_map_type"] in [RML_BLANK_NODE, RML_PARENT_TRIPLES_MAP]:
                continue
            iterator = rule["iterator"]
            for value in rule["subject_references"] + rule["predicate_references"] + rule["object_references"]:
                splitted = value.split(".")
                predecessors = '.'.join(splitted[:-1])
                path = f"{iterator}.{predecessors}['{splitted[-1]}']"
                template.add_path(path)
        return template
    elif source_type == "CSV":
        return CSVTemplate()
    elif source_type == "RDB":
        return RDBTemplate(db_url)
    else:
        raise ValueError(f"Unsupported source type: {source_type}")


def extract_db_config(config: morph_kgc.config.Config) -> dict:
    """Extract database configuration from Morph-KGC config."""
    db_configs = {}
    for section in config.get_data_sources_sections():
        try:
            db_url = config.get_db_url(section)
            if db_url:
                db_configs[section] = {'db_url': db_url}
        except Exception as e:
            get_logger().warning(f"Could not extract database URL for section {section}: {str(e)}")
    
    if not db_configs:
        raise ValueError("No valid database configurations found in Morph-KGC config")
    return db_configs


def test_logging_setup(test_id: str):
    """Set up logging for test cases."""
    if not os.path.exists(TEST_LOG_FOLDER):
        os.mkdir(TEST_LOG_FOLDER)

    log_file = TEST_LOG_FOLDER / f"{test_id}.log"
    if os.path.exists(log_file):
        os.remove(log_file)
        
    logger = get_logger()
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(name)s - %(levelname)s - %(message)s")
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def logging_setup():
    """Set up general logging."""
    if os.path.exists("inversion.log"):
        os.remove("inversion.log")

    logger = get_logger()
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(name)s - %(levelname)s - %(message)s")
    
    # File handler
    file_handler = logging.FileHandler("inversion.log")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    logger.propagate = False


def inversion(config_file: str | pathlib.Path, test_id: str = None, dest_db_url: str = None, 
              sparql_endpoint: str = None, use_virtuoso: bool = False) -> dict[str, dict[str, str]]:
    """
    Main inversion function.
    
    Args:
        config_file: Path to Morph-KGC configuration file
        test_id: Optional test ID for logging
        dest_db_url: Optional destination database URL
        sparql_endpoint: Optional SPARQL endpoint URL
        use_virtuoso: Whether to use Virtuoso for RDF processing
        
    Returns:
        Dictionary mapping source names to inverted results
    """
    results = {}
    
    if test_id is not None:
        test_logging_setup(test_id)
        
    config = load_config_from_argument(config_file)
    
    try:
        mappings, _ = retrieve_mappings(config)
    except ValueError as e:
        if str(e) == "Not supported query type!":
            get_logger().warning("Invalid SQL query in mapping")
        return results
    except KeyError as e:
        if str(e) == "'object_map'":
            get_logger().warning("Mapping with missing information. Skipping.")
        return results
        
    try:
        if sparql_endpoint:
            # Use provided SPARQL endpoint
            url = config.get_output_file()
            
            if use_virtuoso:
                endpoint = VirtuosoEndpoint(sparql_endpoint, rdf_file_to_load=url)
            else:
                endpoint = RemoteEndpoint(sparql_endpoint, rdf_file_to_load=url)
        else:
            # Use local SPARQL endpoint with RDF output file
            endpoint = EndpointFactory.create_from_url(config.get_output_file())
    except FileNotFoundError:
        get_logger().warning("Output file not found. Skipping inversion.")
        return results
        
    insert_columns(mappings)
    db_configs = extract_db_config(config)

    for table_name, source_rules in mappings.groupby("logical_source_value"):
        source_section = source_rules.iloc[0].get('source_section', 'DataSource1')
        db_config = db_configs.get(source_section, db_configs.get('DataSource1', {}))
        template_db_url = dest_db_url if dest_db_url else db_config.get('db_url')
        template = generate_template(source_rules, template_db_url)
        
        source_data, sparql_query = retrieve_data(mappings, source_rules, endpoint, decode_columns=True)
        
        if source_data is None:
            results[table_name] = {"inverted_query": "", "sparql_query": ""}
            get_logger().warning(f"No data generated for {table_name}")
            continue
            
        try:
            filled_source = template.fill_data(source_data, table_name)
            results[table_name] = {
                "inverted_query": filled_source,
                "sparql_query": sparql_query
            }
        except AttributeError as e:
            get_logger().error(f"Error while filling template: {e}")
            raise e
            
    return results