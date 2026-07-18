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

## Dependencies

The library pulls in a few things worth knowing about:

- [**morph-kgc**](https://morph-kgc.readthedocs.io/) parses [R2RML](https://www.w3.org/TR/r2rml/) mapping documents into an internal representation the algorithm operates on.
- [**pyoxigraph**](https://pyoxigraph.readthedocs.io/) provides the local RDF store and SPARQL engine.
- [**pandas**](https://pandas.pydata.org/) handles tabular data throughout the pipeline.
- [**SQLAlchemy**](https://www.sqlalchemy.org/) manages database connections when writing output to SQL databases.
