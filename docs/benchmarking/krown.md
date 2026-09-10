<!--
SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>

SPDX-License-Identifier: ISC
-->

# KROWN

The [KROWN](https://github.com/kg-construct/KROWN) benchmark measures RDF materialization and relational data recovery in 72 cases. They cover source data, mappings, Named Graphs, and joins.

| Suite | Scenarios | Parameters covered |
| --- | ---: | --- |
| `raw` | 11 | Row count, column count, and cell size |
| `duplicates-empty` | 10 | Percentages of duplicate rows and empty values |
| `mappings` | 7 | Triples Maps and Predicate-Object Maps |
| `named-graphs` | 24 | Static and dynamic graph maps in Subject Maps and Predicate-Object Maps |
| `joins` | 20 | Relations, join conditions, and duplicate join results |

## Run the benchmark

Supply each required parameter when you run KROWN, as this example shows for three iterations across all suites:

```bash
make benchmark-krown \
  I=3 \
  MODE=roundtrip \
  SUITES=all \
  FORWARD_ENGINE=rmlmapper \
  INVERSION_ENGINE=kgi \
  SOUFFLE_MODE=rdf \
  INTERVAL=0.1
```

The Makefile exposes these options:

| Option | Accepted values | Required |
| --- | --- | --- |
| `I` | Odd integer greater than or equal to 3 | Yes |
| `MODE` | `forward`, `backward`, `roundtrip` | Yes |
| `SUITES` | `all` or a comma-separated subset of `raw`, `duplicates-empty`, `mappings`, `named-graphs`, `joins` | Yes |
| `SCENARIO` | Exact generated scenario name | No |
| `RESUME` | Directory of an interrupted benchmark session | No |
| `FORWARD_ENGINE` | `rmlmapper`, `morphkgc`, `souffle` | Yes |
| `INVERSION_ENGINE` | `kgi`, `souffle` | Yes |
| `SOUFFLE_MODE` | `rdf`, `provenance`, `hybrid` | Yes |
| `INTERVAL` | Positive number of seconds between system metric samples | Yes |

`forward` measures materialization only. `backward` measures inversion after an unmeasured materialization prepares its input. `roundtrip` measures paired materialization and inversion runs, then validates the reconstructed data by materializing it again and comparing the RDF datasets. Generation, database setup, validation, and cooldown time are excluded from all reported durations.

Select suites or a single scenario with the same command:

```bash
make benchmark-krown I=3 MODE=forward SUITES=raw,mappings \
  FORWARD_ENGINE=rmlmapper INVERSION_ENGINE=kgi SOUFFLE_MODE=rdf INTERVAL=0.1
make benchmark-krown I=3 MODE=backward SUITES=named-graphs \
  FORWARD_ENGINE=rmlmapper INVERSION_ENGINE=kgi SOUFFLE_MODE=rdf INTERVAL=0.1 \
  SCENARIO=namedgraph_0SM-NG_5POM-NG_1TM_1POM_True
```

`RESUME` accepts the session directory from an interrupted run. Its mode, iteration count, sampling interval, and engines must match the original command. The selected suites must include every scenario already recorded in the partial results.

## Interpret results

Each scenario reports one of these outcomes:

| Outcome | Meaning |
| --- | --- |
| `FORWARD` | Forward materialization completed and its RDF output passed validation. No inversion was requested. |
| `FULL` | Every source table, row, column, value, and multiplicity was reconstructed, and the RDF round trip matched. |
| `PARTIAL` | The inversion is partial but deterministic: the mapping and RDF graph identify one recoverable subset of the source data, and that subset reproduces the same RDF dataset. |
| `AMBIGUOUS` | The inversion is partial and non-deterministic: the mapping and RDF graph allow recoverable values to be assigned to the source in more than one way, so no unique partial reconstruction can be selected. |
| `NON_INVERTIBLE` | The mapping leaves no source column that can be reconstructed. |
| `OUT_OF_MEMORY` | A memory limit was reached, or the scenario was skipped because this failure is expected. |
| `TIMEOUT` | A time limit was reached, or the scenario was skipped because this failure is expected. |

`RawData` and the 0% duplicate and empty-value scenarios should return `FULL` because their RDF graphs retain all source data. Cases above 0% should return `PARTIAL` because each RDF graph defines one source subset, although it loses rows or empty values. Join cases should return `AMBIGUOUS` because their keys cannot be linked to one source row. Other suites may return `PARTIAL` when the mapping omits source data but still defines one subset. `AMBIGUOUS` means that the inverse problem has several valid answers, so inversion leaves unresolved values out.

Forward time and inversion time measure their respective stages. Total time is their sum when both are measured. Inversion overhead is available in `roundtrip` mode and is calculated as `inversion time / forward time × 100`.

Inversion throughput is the number of source rows or source cells divided by inversion time. It describes the input represented by the scenario, including data that the mapping does not expose. Aggregated results report the mean, median, standard deviation, quartiles, range, outliers, and a 95% confidence interval for the mean.

CPU, RAM, swap, disk, and network measurements cover the whole host. Run comparisons without unrelated workloads.

## Saved output

Each run creates a timestamped session under `benchmarks/krown/results/`. The session contains raw results, aggregated statistics, timing plots, and system resource statistics. Interrupted runs save partial results in the same location and can be continued with `RESUME`.
