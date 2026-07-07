<!--
SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>

SPDX-License-Identifier: ISC
-->

# Benchmarking

The benchmark uses [KROWN](https://github.com/kg-construct/KROWN) `Mappings` scenarios. Each run loads generated data into PostgreSQL, materializes RDF with RMLMapper, runs KGI inversion, and validates that only the unmapped `id` column is lost.

## Run

Initialize the KROWN submodule once:

```bash
git submodule update --init --recursive
```

Run the benchmark with validation:

```bash
docker compose -f docker-compose.benchmark.yml run --rm benchmark benchmark --iterations 10 --validate
```

Stop benchmark services:

```bash
docker compose -f docker-compose.benchmark.yml down --remove-orphans
```

## PyOxyGraph run with ten iterations (2026-07-07)

| Scenario | Runs | Rows | Execution time | RMLMapper | Inversion | Inversion overhead | Outcome |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `mappings_2_3` | 10/10 | 1,000 | 2.22s +/- 0.28s | 1.18s +/- 0.03s | 0.83s +/- 0.31s | 71.1% +/- 27.6% | PARTIAL (expected) |
| `mappings_3_5` | 10/10 | 10,000 | 4.49s +/- 0.39s | 2.58s +/- 0.09s | 1.01s +/- 0.06s | 39.1% +/- 2.0% | PARTIAL (expected) |
| `mappings_5_8` | 10/10 | 50,000 | 33.86s +/- 1.35s | 21.91s +/- 1.07s | 6.30s +/- 0.26s | 28.8% +/- 1.9% | PARTIAL (expected) |
