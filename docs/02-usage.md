<!--
SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>

SPDX-License-Identifier: ISC
-->

# Usage

The tool exposes a single entry point: the `reconstruct()` function. It takes an R2RML or RML mapping file and an RDF graph, runs the inversion, and returns the reconstructed data.

## Basic invocation

```python
import kgi

result = kgi.reconstruct(
    mapping="mapping.ttl",
    rdf_graph="output.nq",
)
```

The function returns a list of `ReconstructedTable` objects. Each object has a `name` attribute with the source table name and a `data` attribute containing a pandas DataFrame with the reconstructed rows.

The rows are materialized automatically when `dest_db_url` is provided.

```python
for table in result:
    print(f"--- {table.name} ---")
    print(table.data)
```

## Using the source database schema

The algorithm infers column types and ordering on its own, but when a source database is available it can read the original schema to resolve ambiguous cases:

```python
result = kgi.reconstruct(
    mapping="mapping.ttl",
    rdf_graph="output.nq",
    source_db_url="postgresql+psycopg2://user:password@localhost:5432/source_db",
)
```

With RML mappings that contain [D2RQ](http://d2rq.org/) database definitions, the connection info is extracted directly from the mapping file. When the mapping includes a block like this:

```turtle
<#DB_source> a d2rq:Database;
  d2rq:jdbcDSN "jdbc:postgresql://localhost:5432/mydb";
  d2rq:username "user";
  d2rq:password "pass" .
```

the JDBC DSN, username, and password are converted to a SQLAlchemy URL and used as `source_db_url` automatically. If you pass `source_db_url` explicitly, it takes precedence over whatever the mapping says.

## Writing output to a separate database

To write the reconstructed tables to a database, pass a [SQLAlchemy](https://www.sqlalchemy.org/) connection string via `dest_db_url`:

```python
result = kgi.reconstruct(
    mapping="mapping.ttl",
    rdf_graph="output.nq",
    dest_db_url="postgresql+psycopg2://user:password@localhost:5432/restored_db",
)
```

## Querying a remote SPARQL endpoint

By default, the function reads RDF from the local file specified by `rdf_graph`. To query a remote endpoint instead:

```python
result = kgi.reconstruct(
    mapping="mapping.ttl",
    rdf_graph="output.nq",
    sparql_endpoint="http://localhost:8890/sparql",
)
```

## Error handling

Not every mapping can be inverted. When the function encounters an unsupported or non-invertible case, it raises an exception:

```python
from kgi import MappingError, UnsupportedMappingError, NonInvertibleError, NoDataError

try:
    result = kgi.reconstruct(mapping="mapping.ttl", rdf_graph="output.nq")
except UnsupportedMappingError as e:
    print(f"Unsupported: {e}")
except MappingError as e:
    print(f"Invalid mapping: {e}")
except NonInvertibleError as e:
    print(f"Non-invertible: {e}")
except NoDataError as e:
    print(f"No data: {e}")
```

The exceptions are:

| Exception | Meaning |
|---|---|
| `UnsupportedMappingError` | The mapping uses SQL queries as logical tables, which the algorithm does not handle. |
| `MappingError` | The mapping document is syntactically invalid or violates the R2RML specification. |
| `NonInvertibleError` | The mapping is valid but the transformation is not reversible. See [limitations](04-limitations.md). |
| `NoDataError` | The SPARQL queries returned no results, or the RDF input file does not exist. |
