---
# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

title: Usage
description: How to use the tool as a Python library.
---

The tool exposes a single entry point: the `reconstruct()` function. It takes an R2RML mapping file and an RDF graph, runs the inversion, and returns the reconstructed data.

## Basic invocation

```python
import kgi

result = kgi.reconstruct(
    mapping="mapping.ttl",
    rdf_graph="output.nq",
)
```

The function returns a dictionary keyed by source table name. Each value is a `ReconstructedTable` with three attributes:

- `sparql_query`: the SPARQL query that was executed against the RDF graph to extract the data.
- `sql`: the SQL statements (CREATE TABLE + INSERT) that reproduce the original table.
- `data`: a pandas DataFrame with the reconstructed rows.

```python
for table_name, table in result.items():
    print(f"--- {table_name} ---")
    print(table.sparql_query)
    print(table.sql)
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
| `NonInvertibleError` | The mapping is valid but the transformation is not reversible. See [limitations](/knowledge-graphs-inversion/concepts/limitations/). |
| `NoDataError` | The SPARQL queries returned no results, or the RDF input file does not exist. |
