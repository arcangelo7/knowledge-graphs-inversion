<!--
SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>

SPDX-License-Identifier: ISC
-->

# Installation

The tool requires Python 3.11, 3.12, or 3.13.

## Install the package

Clone the repository and install with [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/arcangelo7/knowledge-graphs-inversion.git
```

```bash
cd knowledge-graphs-inversion
```

```bash
uv sync
```

Conformance tests and benchmarks also require [Docker](https://www.docker.com/) for the PostgreSQL and GTFS MySQL databases and Java 21 or newer for RMLMapper v8.1.0. The root Makefile initializes submodules automatically for benchmark runs:

```bash
make benchmark-krown I=1 KROWN_SUITES=mappings
```

Omit `KROWN_SUITES` to run every official-scale KROWN scenario.

```bash
make benchmark-gtfs I=10 S=1,5,10
```

## Dependencies

The library pulls in a few things worth knowing about:

- [**morph-kgc**](https://morph-kgc.readthedocs.io/) parses [R2RML](https://www.w3.org/TR/r2rml/) mapping documents into an internal representation the algorithm operates on.
- [**pyoxigraph**](https://pyoxigraph.readthedocs.io/) provides the in-memory RDF store and SPARQL engine for local graph queries.
- [**pandas**](https://pandas.pydata.org/) handles tabular data throughout the pipeline.
- [**SQLAlchemy**](https://www.sqlalchemy.org/) manages database connections when writing output to SQL databases.
