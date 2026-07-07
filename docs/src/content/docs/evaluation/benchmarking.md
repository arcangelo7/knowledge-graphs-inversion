---
# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

title: Benchmarking
description: Performance evaluation with KROWN Mappings scenarios.
---

The project measures inversion performance on scenarios produced by the [KROWN](https://github.com/kg-construct/KROWN) data generator. The runner invokes KROWN's `exgentool`, loads the relational data into PostgreSQL, materializes RDF with RMLMapper, runs the inversion, and collects execution times.

## Setup

The benchmark requires the KROWN submodule:

```bash
git submodule update --init --recursive
```

## Scenario class

- `mappings_2_3`: 2 TriplesMaps, 3 PredicateObjectMaps, 1K rows.
- `mappings_3_5`: 3 TriplesMaps, 5 PredicateObjectMaps, 10K rows.
- `mappings_5_8`: 5 TriplesMaps, 8 PredicateObjectMaps, 50K rows.

In these scenarios, triples map `i` uses subject template `http://ex.com/table/{p_i}`, while all triples maps repeat the same PredicateObjectMaps over `p1..pM`. The source table also contains an `id` column, but the KROWN `Mappings` generator does not reference it in the generated mapping. The expected reconstruction is therefore partial: KGI should recover the mapped property columns and lose only `id`.

The `Mappings` scenarios with `tms >= poms` are excluded because at least one column is used only to build a subject IRI and never appears as an object value. Since those subject-only columns all use the same IRI shape and the same outgoing predicates, the RDF graph does not identify which source column each subject came from.

## Running benchmarks

Benchmarks run through a dedicated Docker Compose file that starts PostgreSQL and a selectable SPARQL backend. The default backend is [Virtuoso](https://virtuoso.openlinksw.com/):

```bash
docker compose -f docker-compose.benchmark.yml up
```

For multiple iterations:

```bash
docker compose -f docker-compose.benchmark.yml run --rm benchmark benchmark --iterations 10
```

To compare with [QLever](https://docs.qlever.dev/):

```bash
docker compose -f docker-compose.benchmark.yml run --rm benchmark benchmark --sparql-backend qlever --iterations 10
```

QLever builds a temporary index from each generated RDF file and starts a local endpoint for that scenario. Virtuoso uses a temporary database directory per inversion, bulk-loads the generated RDF file, and removes the directory after the scenario. The benchmark path does not use SPARQL Update to insert or clear RDF data.

To stop services:

```bash
docker compose -f docker-compose.benchmark.yml down --remove-orphans
```

## Results

Benchmark output goes to `benchmarks/krown/results/` and includes:

- Execution times and outcome classification per scenario.
- Statistical summaries when running multiple iterations.
- Box plot visualizations for time distributions.
- Data and mapping file sizes.
- Counts of TriplesMaps and PredicateObjectMaps per scenario.

Validation expects every completed scenario to lose only the `id` column:

```bash
docker compose -f docker-compose.benchmark.yml run --rm benchmark benchmark --iterations 10 --validate
docker compose -f docker-compose.benchmark.yml run --rm benchmark benchmark --sparql-backend qlever --iterations 10 --validate
```
