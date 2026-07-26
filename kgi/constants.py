# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

"""Constants used throughout the KGI library."""

import re

from pyoxigraph import NamedNode

RML_BLANK_NODE = "http://w3id.org/rml/BlankNode"
RML_CONSTANT = "http://w3id.org/rml/constant"
RML_IRI = "http://w3id.org/rml/IRI"
RML_LITERAL = "http://w3id.org/rml/Literal"
RML_PARENT_TRIPLES_MAP = "http://w3id.org/rml/parentTriplesMap"
RML_REFERENCE = "http://w3id.org/rml/reference"
RML_DEFAULT_GRAPH = "http://w3id.org/rml/defaultGraph"
RML_SOURCE = "http://w3id.org/rml/source"
RML_TABLE_NAME = "http://w3id.org/rml/tableName"
RML_TEMPLATE = "http://w3id.org/rml/template"

RR_SUBJECT_MAP = NamedNode("http://www.w3.org/ns/r2rml#subjectMap")
RR_TERM_TYPE = NamedNode("http://www.w3.org/ns/r2rml#termType")
RR_LITERAL = NamedNode("http://www.w3.org/ns/r2rml#Literal")
RR_COLUMN = NamedNode("http://www.w3.org/ns/r2rml#column")
RR_TEMPLATE = NamedNode("http://www.w3.org/ns/r2rml#template")
RR_CHILD = NamedNode("http://www.w3.org/ns/r2rml#child")
RR_PARENT = NamedNode("http://www.w3.org/ns/r2rml#parent")
RR_PARENT_TRIPLES_MAP = NamedNode("http://www.w3.org/ns/r2rml#parentTriplesMap")
RR_TABLE_NAME = NamedNode("http://www.w3.org/ns/r2rml#tableName")
RR_LOGICAL_TABLE = NamedNode("http://www.w3.org/ns/r2rml#logicalTable")
RR_SQL_QUERY = NamedNode("http://www.w3.org/ns/r2rml#sqlQuery")
RR_TRIPLES_MAP = NamedNode("http://www.w3.org/ns/r2rml#TriplesMap")

RML_OLD_REFERENCE = NamedNode("http://semweb.mmlab.be/ns/rml#reference")
RML_OLD_LOGICAL_SOURCE = NamedNode("http://semweb.mmlab.be/ns/rml#logicalSource")
RML_OLD_QUERY = NamedNode("http://semweb.mmlab.be/ns/rml#query")

RML_REFERENCE_NODE = NamedNode(RML_REFERENCE)
RML_TEMPLATE_NODE = NamedNode(RML_TEMPLATE)
RML_CHILD = NamedNode("http://w3id.org/rml/child")
RML_PARENT = NamedNode("http://w3id.org/rml/parent")
RML_LOGICAL_SOURCE = NamedNode("http://w3id.org/rml/logicalSource")
RML_SUBJECT_MAP = NamedNode("http://w3id.org/rml/subjectMap")
RML_ITERATOR = NamedNode("http://w3id.org/rml/iterator")
RML_REFERENCE_FORMULATION = NamedNode("http://w3id.org/rml/referenceFormulation")
RML_SQL2008_TABLE = NamedNode("http://w3id.org/rml/SQL2008Table")
RML_SQL2008_QUERY = NamedNode("http://w3id.org/rml/SQL2008Query")
RML_QUERY = NamedNode("http://w3id.org/rml/query")

RDF_TYPE = NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")

D2RQ_DATABASE = NamedNode("http://www.wiwiss.fu-berlin.de/suhl/bizer/D2RQ/0.1#Database")
D2RQ_JDBC_DSN = NamedNode("http://www.wiwiss.fu-berlin.de/suhl/bizer/D2RQ/0.1#jdbcDSN")
D2RQ_USERNAME = NamedNode("http://www.wiwiss.fu-berlin.de/suhl/bizer/D2RQ/0.1#username")
D2RQ_PASSWORD = NamedNode("http://www.wiwiss.fu-berlin.de/suhl/bizer/D2RQ/0.1#password")

JDBC_DRIVERS: dict[str, str] = {
    "postgresql": "postgresql+psycopg2",
    "mysql": "mysql+pymysql",
}

# Placeholders in template values already normalized by morph-kgc, e.g. {Name}
REF_TEMPLATE_REGEX = r"{([^{}]*)}"
# Placeholders in raw mapping text, where identifiers may be delimited, e.g. {\"Name\"}
TEMPLATE_COLUMN_REGEX = re.compile(r'\{\\?"?\'?([^"\'{}\\]+)\\?"?\'?\}')
