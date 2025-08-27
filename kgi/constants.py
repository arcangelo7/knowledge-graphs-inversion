"""Constants used throughout the KGI library."""

import pathlib

# Query types
QUERY_MINIMAL = 0
QUERY_REDUCED = 1
QUERY_FULL = 2

# RML constants from morph_kgc
RML_BLANK_NODE = "http://w3id.org/rml/BlankNode"
RML_CONSTANT = "http://w3id.org/rml/constant"
RML_IRI = "http://w3id.org/rml/IRI"
RML_LITERAL = "http://w3id.org/rml/Literal"
RML_PARENT_TRIPLES_MAP = "http://w3id.org/rml/parentTriplesMap"
RML_REFERENCE = "http://w3id.org/rml/reference"
RML_TEMPLATE = "http://w3id.org/rml/template"

# Regex patterns
REF_TEMPLATE_REGEX = r"{([^{}]*)}"

# Paths
TEST_CASES_PATH = pathlib.Path(__file__).parent.parent / "rml-test-cases" / "test-cases"
TEST_LOG_FOLDER = pathlib.Path(__file__).parent.parent / "individual-logs"

# Morph-KGC configuration template
MORPH_CONFIG = """
    [CONFIGURATION]
    # INPUT
    na_values=,#N/A,N/A,#N/A N/A,n/a,NA,<NA>,#NA,NULL,null,NaN,nan,None

    # OUTPUT
    output_file=output.nq
    output_dir=
    output_format=N-TRIPLES
    only_printable_characters=no
    safe_percent_encoding=

    # MAPPINGS
    mapping_partitioning=PARTIAL-AGGREGATIONS
    infer_sql_datatypes=no

    # MULTIPROCESSING
    number_of_processes=

    # LOGS
    logging_level=WARNING
    logs_file=


    [DataSource1]
    mappings: mapping.ttl
"""