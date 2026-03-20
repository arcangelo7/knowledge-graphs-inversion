---
title: Installation
description: How to install and set up the tool.
---

The tool requires Python 3.11, 3.12, or 3.13.

## Installation

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

Conformance tests and benchmarks also require [Docker](https://www.docker.com/) for the PostgreSQL databases, and the git submodules:

```bash
git submodule update --init --recursive
```

## Dependencies

The library pulls in a few things worth knowing about:

- [**morph-kgc**](https://morph-kgc.readthedocs.io/) parses [R2RML](https://www.w3.org/TR/r2rml/) mapping documents into an internal representation the algorithm operates on.
- [**pyoxigraph**](https://pyoxigraph.readthedocs.io/) provides the in-memory RDF store and SPARQL engine for local graph queries.
- [**pandas**](https://pandas.pydata.org/) handles tabular data throughout the pipeline.
- [**SQLAlchemy**](https://www.sqlalchemy.org/) manages database connections when writing output to SQL databases.
