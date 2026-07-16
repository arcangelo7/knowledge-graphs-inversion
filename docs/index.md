<!--
SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>

SPDX-License-Identifier: ISC
-->

# Knowledge Graphs Inversion

## What does it do?

[R2RML](https://www.w3.org/TR/r2rml/) and [RML](https://kg-construct.github.io/rml-core/spec/docs/) transform relational databases into RDF graphs. This tool does the opposite: given an RDF graph and the mapping that produced it, it reconstructs the original relational data.

The tool reads the mapping document, generates SPARQL queries that reverse each mapping rule, executes them against the RDF graph (N-Triples or N-Quads), and materializes the reconstructed tables in a destination database.
