<!--
SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>

SPDX-License-Identifier: ISC
-->

# Usage

The tool exposes a single entry point: the `reconstruct()` function. It takes an R2RML or RML mapping file, an RDF graph, and a destination database, then materializes the reconstructed tables in that database.

## Basic invocation

```python
import kgi

kgi.reconstruct(
    mapping="mapping.ttl",
    rdf_graph="output.nq",
    dest_db_url="postgresql+psycopg2://user:password@localhost:5432/restored_db",
)
```

`dest_db_url` is required. The function writes the reconstructed rows to that database and returns `None`.

## Using the source database schema

The algorithm infers column types and ordering on its own, but when a source database is available it can read the original schema to resolve ambiguous cases:

```python
kgi.reconstruct(
    mapping="mapping.ttl",
    rdf_graph="output.nq",
    dest_db_url="postgresql+psycopg2://user:password@localhost:5432/restored_db",
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

## Destination database

Pass the destination as a [SQLAlchemy](https://www.sqlalchemy.org/) connection string via `dest_db_url`:

```python
kgi.reconstruct(
    mapping="mapping.ttl",
    rdf_graph="output.nq",
    dest_db_url="postgresql+psycopg2://user:password@localhost:5432/restored_db",
)
```

## Error handling

Not every mapping can be inverted. When the function encounters an unsupported or non-invertible case, it raises an exception:

```python
from kgi import MappingError, NoDataError, NonInvertibleError, UnsupportedMappingError

try:
    kgi.reconstruct(
        mapping="mapping.ttl",
        rdf_graph="output.nq",
        dest_db_url="postgresql+psycopg2://user:password@localhost:5432/restored_db",
    )
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
| `NonInvertibleError` | The mapping is valid but leaves no recoverable column for some table. Columns the graph cannot attribute are dropped from the reconstruction instead, without raising. See [limitations](limitations). |
| `NoDataError` | The SPARQL queries returned no results, or the RDF input file does not exist. |
