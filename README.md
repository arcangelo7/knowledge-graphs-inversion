# Knowledge Graphs Inversion

[![Run tests](https://github.com/arcangelo7/knowledge-graphs-inversion/actions/workflows/test.yml/badge.svg)](https://github.com/arcangelo7/knowledge-graphs-inversion/actions/workflows/test.yml)
[![Coverage](https://arcangelo7.github.io/knowledge-graphs-inversion/coverage/coverage-badge.svg)](https://arcangelo7.github.io/knowledge-graphs-inversion/coverage/)
[![License: ISC](https://img.shields.io/badge/license-ISC-blue.svg)](https://opensource.org/licenses/ISC)
[![REUSE](https://api.reuse.software/badge/github.com/arcangelo7/knowledge-graphs-inversion)](https://api.reuse.software/info/github.com/arcangelo7/knowledge-graphs-inversion)
[![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

Given an RDF graph and the [R2RML](https://www.w3.org/TR/r2rml/) or [RML](https://kg-construct.github.io/rml-core/spec/docs/) mapping that produced it, this tool reconstructs the original relational data. It parses the mapping document, generates SPARQL queries that reverse each mapping rule, and materializes the reconstructed rows in a relational database when a destination URL is provided.

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

result = kgi.reconstruct(
    mapping="mapping.ttl",
    rdf_graph="output.nq",
    source_db_url="postgresql+psycopg2://user:pass@localhost:5432/source",
    dest_db_url="postgresql+psycopg2://user:pass@localhost:5433/dest",
)
```

The result is a list of `ReconstructedTable` objects. Each object has a `name` attribute with the source table name and a `data` attribute containing a pandas DataFrame with the reconstructed rows. The rows are materialized automatically when `dest_db_url` is provided.

```python
for table in result:
    print(table.name)
    print(table.data)
```

`source_db_url` is optional and used to read the original column types and ordering. When using RML mappings with [D2RQ](http://d2rq.org/) database definitions, the connection info is extracted automatically from the mapping itself, no need to pass `source_db_url` at all. `dest_db_url` is optional and sets the target database for materialized rows.

Local RDF queries use PyOxyGraph by default. It is the recommended choice for small RDF graphs. For larger graphs with many triples, pass `backend="qlever"` to query through a temporary QLever index:

```python
result = kgi.reconstruct(
    mapping="mapping.ttl",
    rdf_graph="output.nq",
    backend="qlever",
)
```

## Testing

Conformance tests require [Docker](https://www.docker.com/) to run the PostgreSQL databases and Java 21 or newer for RMLMapper v8.1.0:

```bash
uv run pytest -v
```

## License

ISC
