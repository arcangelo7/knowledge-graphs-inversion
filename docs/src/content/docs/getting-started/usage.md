---
# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

title: Usage
description: How to use the tool as a Python library.
---

The tool exposes a single entry point: the `inversion()` function in `kgi.core`. It takes a [Morph-KGC](https://morph-kgc.readthedocs.io/) configuration file, runs the inversion, and returns the reconstructed data.

## Morph-KGC configuration

The inversion relies on a [Morph-KGC](https://morph-kgc.readthedocs.io/) configuration file to locate the mapping document, the RDF output, and the source database. See the [Morph-KGC documentation](https://morph-kgc.readthedocs.io/en/stable/documentation/#configuration) for the full reference.

```ini
[CONFIGURATION]
output_file=output.nq
output_format=N-QUADS

[DataSource1]
mappings: mapping.ttl
db_url: postgresql://user:password@localhost:5432/source_db
```

The `db_url` in the configuration is optional. The algorithm infers column types and ordering on its own, but when `db_url` is provided it can read the original database schema to resolve ambiguous cases.

## Basic invocation

```python
from kgi.core import inversion

result = inversion(config_file="morph_kgc_config.ini")
```

The function returns a dictionary keyed by source table name. Each entry contains:

- `sparql_query`: the SPARQL query that was executed against the RDF graph to extract the data.
- `inverted_query`: the SQL statements (CREATE TABLE + INSERT) that reproduce the original table.

```python
for table_name, data in result.items():
    print(f"--- {table_name} ---")
    print(data["sparql_query"])
    print(data["inverted_query"])
```

## Writing output to a separate database

By default, the reconstructed tables are written back to the same database specified in the configuration. To write them to a different database, pass a [SQLAlchemy](https://www.sqlalchemy.org/) connection string via `dest_db_url`:

```python
result = inversion(
    config_file="morph_kgc_config.ini",
    dest_db_url="postgresql://user:password@localhost:5432/restored_db",
)
```

## Querying a remote SPARQL endpoint

By default, the function reads RDF from a local file (the `output_file` in the Morph-KGC config). To query a remote endpoint instead:

```python
result = inversion(
    config_file="morph_kgc_config.ini",
    sparql_endpoint="http://localhost:8890/sparql",
)
```

## Handling special cases

Not every mapping can be inverted. When the function encounters an unsupported or non-invertible case, it returns a status dictionary instead of the normal result:

```python
result = inversion(config_file="morph_kgc_config.ini")

if "__status__" in result:
    print(f"Status: {result['__status__']}")
    print(f"Reason: {result['__reason__']}")
```

The possible statuses are:

| Status | Meaning |
|---|---|
| `not_supported` | The mapping uses SQL queries as logical tables, which the algorithm does not handle. |
| `mapping_error` | The mapping document is syntactically invalid or violates the R2RML specification. |
| `non_invertible` | The mapping is valid but the transformation is not reversible. See [limitations](/knowledge-graphs-inversion/concepts/limitations/). |
| `no_input_file` | The RDF input file referenced in the config does not exist. |
| `no_data_generated` | The SPARQL queries returned no results. |
