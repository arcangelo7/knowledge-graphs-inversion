<!--
SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>

SPDX-License-Identifier: ISC
-->

# Benchmarking

The benchmark suite includes [KROWN](https://github.com/kg-construct/KROWN) `Mappings` scenarios and [GTFS Bench](https://github.com/oeg-upm/gtfs-bench) generated transit scenarios. Each run loads generated data into PostgreSQL, materializes RDF with RMLMapper, runs KGI inversion, and validates reconstructed tables when inversion completes.

KROWN scenarios are expected to be partial because the unmapped `id` column is lost. GTFS scenarios use the official generated data and the official R2RML mapping without altering either one; the benchmark records the observed KGI outcome, including non-invertible or unsupported mappings.

## Run

The root Makefile provides the supported entry points:

```bash
make benchmark-krown I=10
```

```bash
make benchmark-gtfs I=10 S=1,5,10
```

```bash
make benchmark-all I=10 S=1,5,10
```

`I` is the number of iterations. `S` is a comma-separated list of GTFS scales.

## PyOxyGraph run with ten iterations (2026-07-07)

| Scenario | Runs | Rows | Execution time | RMLMapper | Inversion | Inversion overhead | Outcome |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `mappings_2_3` | 10/10 | 1,000 | 2.22s +/- 0.28s | 1.18s +/- 0.03s | 0.83s +/- 0.31s | 71.1% +/- 27.6% | PARTIAL (expected) |
| `mappings_3_5` | 10/10 | 10,000 | 4.49s +/- 0.39s | 2.58s +/- 0.09s | 1.01s +/- 0.06s | 39.1% +/- 2.0% | PARTIAL (expected) |
| `mappings_5_8` | 10/10 | 50,000 | 33.86s +/- 1.35s | 21.91s +/- 1.07s | 6.30s +/- 0.26s | 28.8% +/- 1.9% | PARTIAL (expected) |
