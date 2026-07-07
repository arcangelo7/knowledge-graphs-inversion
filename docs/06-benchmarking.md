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

Run the benchmark with QLever and validation:

```bash
docker compose -f docker-compose.benchmark.yml run --rm benchmark benchmark --sparql-backend qlever --iterations 10 --validate
```

Stop benchmark services:

```bash
docker compose -f docker-compose.benchmark.yml down --remove-orphans
```

## QLever run with ten iterations (2026-07-07)

| Scenario | Runs | Rows | Execution time | RMLMapper | Inversion | Inversion overhead | Outcome |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `mappings_2_3` | 10/10 | 1,000 | 2.30s +/- 0.02s | 1.17s +/- 0.02s | 0.91s +/- 0.05s | 77.4% +/- 5.0% | PARTIAL (expected) |
| `mappings_3_5` | 10/10 | 10,000 | 5.05s +/- 0.08s | 2.53s +/- 0.04s | 1.81s +/- 0.05s | 71.4% +/- 2.2% | PARTIAL (expected) |
| `mappings_5_8` | 10/10 | 50,000 | 35.13s +/- 1.03s | 22.27s +/- 1.14s | 7.37s +/- 0.15s | 33.2% +/- 1.6% | PARTIAL (expected) |
