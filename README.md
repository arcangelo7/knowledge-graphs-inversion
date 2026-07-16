# Knowledge Graphs Inversion

[![Run tests](https://github.com/arcangelo7/knowledge-graphs-inversion/actions/workflows/test.yml/badge.svg)](https://github.com/arcangelo7/knowledge-graphs-inversion/actions/workflows/test.yml)
[![Coverage](https://arcangelo7.github.io/knowledge-graphs-inversion/coverage/coverage-badge.svg)](https://arcangelo7.github.io/knowledge-graphs-inversion/coverage/)
[![License: ISC](https://img.shields.io/badge/license-ISC-blue.svg)](https://opensource.org/licenses/ISC)
[![REUSE](https://api.reuse.software/badge/github.com/arcangelo7/knowledge-graphs-inversion)](https://api.reuse.software/info/github.com/arcangelo7/knowledge-graphs-inversion)
[![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

Given an RDF graph and the [R2RML](https://www.w3.org/TR/r2rml/) or [RML](https://kg-construct.github.io/rml-core/spec/docs/) mapping that produced it, this tool reconstructs the original relational data. It parses the mapping document, generates SPARQL queries that reverse each mapping rule, and materializes the reconstructed rows in a destination database.

Full documentation at [arcangelo7.github.io/knowledge-graphs-inversion](https://arcangelo7.github.io/knowledge-graphs-inversion/).

## Quick start

Requires Python 3.11, 3.12, or 3.13 and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/arcangelo7/knowledge-graphs-inversion.git
```

```bash
cd knowledge-graphs-inversion && uv sync
```

```python
import kgi

kgi.reconstruct(
    mapping="mapping.ttl",
    rdf_graph="output.nq",
    dest_db_url="postgresql+psycopg2://user:pass@localhost:5433/dest",
    source_db_url="postgresql+psycopg2://user:pass@localhost:5432/source",
)
```

`dest_db_url` is required and identifies the database that receives the reconstructed tables. The function returns `None`. `source_db_url` is optional and is used to read the original column types and ordering. When an RML mapping contains a [D2RQ](http://d2rq.org/) database definition, its connection information is used as `source_db_url` unless an explicit value is passed. RDF queries are executed locally with PyOxigraph.

## Testing

Conformance tests require [Docker](https://www.docker.com/) to run the PostgreSQL databases and Java 21 or newer for RMLMapper v8.1.0. Use the root Makefile entry point:

```bash
make test-conformance
```

## Benchmarking

Benchmark targets initialize submodules, run the needed Docker Compose services, validate completed inversions, and clean up Compose services on exit:

```bash
make benchmark-krown I=1 KROWN_SUITES=mappings
make benchmark-krown I=1 KROWN_SCENARIO=namedgraph_0SM-NG_5POM-NG_1TM_1POM_True
make benchmark-gtfs I=10 S=1,5,10
make benchmark-all I=1 S=1,5,10 KROWN_SUITES=mappings
```

The KROWN runner supports `raw`, `mappings`, `named-graphs`, and `joins`. Running without `KROWN_SUITES` selects every official-scale scenario. Set `KROWN_SCENARIO` to an exact scenario name from the benchmark output to rerun only that scenario.

## License

ISC
