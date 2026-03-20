---
title: Benchmarking
description: Performance evaluation with the KROWN benchmark framework.
---

The project integrates the [KROWN benchmark framework](https://github.com/kg-construct/KROWN) to measure inversion performance across different data scales and mapping complexities. KROWN generates synthetic relational data and mappings, runs the inversion, and collects execution times.

## Setup

Initialize the submodule:

```bash
git submodule update --init --recursive
```

## Running benchmarks

Benchmarks run through a dedicated Docker Compose file that spins up PostgreSQL and (optionally) a [Virtuoso](https://virtuoso.openlinksw.com/) SPARQL endpoint:

```bash
docker compose -f docker-compose.benchmark.yml up
```

### Multiple iterations

For statistically meaningful results, run multiple iterations. The framework computes mean, median, standard deviation, confidence intervals, and generates box plots:

```bash
docker compose -f docker-compose.benchmark.yml run benchmark benchmark --iterations 10
```

### Without Virtuoso

To skip the Virtuoso endpoint and query RDF files directly in memory with [pyoxigraph](https://pyoxigraph.readthedocs.io/):

```bash
docker compose -f docker-compose.benchmark.yml run benchmark benchmark --no-virtuoso
```

### Stopping services

```bash
docker compose -f docker-compose.benchmark.yml down
```

## Results

Benchmark output goes to `benchmarks/krown/results/` and includes:

- Execution times per scenario (JSON)
- Statistical summaries when running multiple iterations
- Box plot visualizations (PNG) for time distributions
- Data and mapping file sizes
- Counts of triples maps and predicate-object maps per scenario

## What gets measured

Each benchmark scenario consists of a generated relational database, an R2RML mapping, and the RDF graph produced by the forward transformation. The benchmark measures the time to parse the mapping, generate the SPARQL queries, execute them against the RDF graph, and reconstruct the relational output. The forward transformation time ([Morph-KGC](https://morph-kgc.readthedocs.io/)) is measured separately to isolate the inversion overhead.

Scenarios scale along two axes: the number of rows in the source tables and the complexity of the mapping (number of triples maps and predicate-object maps).

## Latest results

Results from 100 iterations with Virtuoso as the SPARQL endpoint, on three scenarios of increasing complexity. The scenario name indicates the number of triples maps and predicate-object maps per triples map (e.g., 3x2 = 3 triples maps with 2 predicate-object maps each):

| Scenario | Triples | Morph-KGC | Inversion | Total |
|---|---|---|---|---|
| 3x2 | 6,000 | 0.89 ± 0.06s | 7.33 ± 3.98s | 8.31 ± 4.02s |
| 5x3 | 150,000 | 1.22 ± 0.06s | 3.05 ± 0.23s | 4.61 ± 0.27s |
| 8x5 | 2,000,000 | 5.63 ± 0.71s | 22.68 ± 4.23s | 30.73 ± 4.24s |
