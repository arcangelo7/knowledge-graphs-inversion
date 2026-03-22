# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

"""Constants used throughout the KGI library."""

from pyoxigraph import NamedNode

RML_BLANK_NODE = "http://w3id.org/rml/BlankNode"
RML_CONSTANT = "http://w3id.org/rml/constant"
RML_IRI = "http://w3id.org/rml/IRI"
RML_LITERAL = "http://w3id.org/rml/Literal"
RML_PARENT_TRIPLES_MAP = "http://w3id.org/rml/parentTriplesMap"
RML_REFERENCE = "http://w3id.org/rml/reference"
RML_DEFAULT_GRAPH = "http://w3id.org/rml/defaultGraph"
RML_SOURCE = "http://w3id.org/rml/source"
RML_TEMPLATE = "http://w3id.org/rml/template"

RR_SUBJECT_MAP = NamedNode("http://www.w3.org/ns/r2rml#subjectMap")
RR_TERM_TYPE = NamedNode("http://www.w3.org/ns/r2rml#termType")
RR_LITERAL = NamedNode("http://www.w3.org/ns/r2rml#Literal")

# Regex patterns
REF_TEMPLATE_REGEX = r"{([^{}]*)}"
