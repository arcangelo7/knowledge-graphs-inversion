<!--
SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>

SPDX-License-Identifier: ISC
-->

# KROWN

The [KROWN](https://github.com/kg-construct/KROWN) benchmark measures RDF materialization and relational data reconstruction across 58 scenarios. It covers changes in source data, mappings, Named Graphs, and joins.

| Suite | Scenarios | Parameters covered |
| --- | ---: | --- |
| `raw` | 11 | Row count, column count, and cell size |
| `mappings` | 7 | Triples Maps and Predicate-Object Maps |
| `named-graphs` | 24 | Static and dynamic graph maps in Subject Maps and Predicate-Object Maps |
| `joins` | 16 | One-to-many, many-to-one, and many-to-many relations, plus join conditions |

Duplicate and empty-value scenarios are excluded because they are non-invertible by definition: RDF does not preserve duplicate rows, and empty values in subject templates can suppress rows.

## Run the benchmark

Run every suite with the default configuration:

```bash
make benchmark-krown
```

The Makefile exposes these options:

| Option | Accepted values | Default |
| --- | --- | --- |
| `I` | Odd integer greater than or equal to 3 | `5` |
| `MODE` | `forward`, `backward`, `roundtrip` | `roundtrip` |
| `SUITES` | `all` or a comma-separated subset of `raw`, `mappings`, `named-graphs`, `joins` | `all` |
| `SCENARIO` | Exact generated scenario name | Not set |
| `RESUME` | Directory of an interrupted benchmark session | Not set |
| `FORWARD_ENGINE` | `rmlmapper`, `souffle`, `morphkgc` | `rmlmapper` |
| `INVERSION_ENGINE` | `kgi`, `souffle` | `kgi` |
| `SOUFFLE_PROVENANCE` | `true`, `false` | `false` |
| `INTERVAL` | Positive number of seconds between system metric samples | `0.1` |

`forward` measures materialization only. `backward` measures inversion after an unmeasured materialization prepares its input. `roundtrip` measures paired materialization and inversion runs, then validates the reconstructed data by materializing it again and comparing the RDF datasets. Generation, database setup, validation, and cooldown time are excluded from all reported durations.

RMLMapper, Soufflé, and Morph-KGC are available for forward materialization. The `kgi` inversion engine reconstructs data with SPARQL queries generated from the mapping. Soufflé inversion can only be paired with Soufflé forward materialization, using `FORWARD_ENGINE=souffle INVERSION_ENGINE=souffle`. Both directions generate and compile a Datalog program within their measured stage. Pairing Soufflé inversion with another forward engine would therefore compare a backward time that includes compilation with a forward time that does not include an equivalent step, making the overhead uninterpretable. Set `SOUFFLE_PROVENANCE=true` only for this pair because Soufflé is the only forward engine that can produce the column provenance consumed by Soufflé inversion. For these reasons, Soufflé inversion is not compared with other forward engines.

Select suites or a single scenario with the same command:

```bash
make benchmark-krown I=3 MODE=forward SUITES=raw,mappings
make benchmark-krown I=3 MODE=backward SUITES=named-graphs SCENARIO=namedgraph_0SM-NG_5POM-NG_1TM_1POM_True
```

To compare Soufflé inversion with and without column provenance, run the same scenario twice:

```bash
make benchmark-krown I=3 MODE=roundtrip FORWARD_ENGINE=souffle INVERSION_ENGINE=souffle SCENARIO=mappings_10_5
make benchmark-krown I=3 MODE=roundtrip FORWARD_ENGINE=souffle INVERSION_ENGINE=souffle SOUFFLE_PROVENANCE=true SCENARIO=mappings_10_5
```

`RESUME` accepts the session directory from an interrupted run. Its mode, iteration count, sampling interval, engines, and provenance setting must match the original command. The selected suites must include every scenario already recorded in the partial results.

## Interpret results

Each scenario reports one of these outcomes:

| Outcome | Meaning |
| --- | --- |
| `FORWARD` | Forward materialization completed and its RDF output passed validation. No inversion was requested. |
| `FULL` | Every source table, row, column, value, and multiplicity was reconstructed, and the RDF round trip matched. |
| `PARTIAL` | The inversion is partial but deterministic: the mapping and RDF graph identify one recoverable subset of the source data, and that subset reproduces the same RDF dataset. |
| `AMBIGUOUS` | The inversion is partial and non-deterministic: the mapping and RDF graph allow recoverable values to be assigned to the source in more than one way, so no unique partial reconstruction can be selected. |
| `NON_INVERTIBLE` | The mapping leaves no source column that can be reconstructed. |
| `OUT_OF_MEMORY` | Materialization or inversion stopped after exhausting its available memory. |
| `TIMEOUT` | Materialization or inversion exceeded its time limit. |

Completed `RawData` scenarios are expected to return `FULL`. Other suites may return `PARTIAL` when the mapping omits source information but still determines a unique recoverable subset. `AMBIGUOUS` describes the inverse problem, not variable program output: the inversion leaves unresolved values out instead of choosing among possible source assignments. This occurs when mapped values cannot be assigned to their source columns, as in joins whose key columns are not emitted. Without Soufflé provenance, it also affects mapping scenarios with more Triples Maps than Predicate-Object Maps. With provenance, every `Mappings` scenario is expected to return `PARTIAL` and complete the RDF round trip.

Forward time and inversion time measure their respective stages. Total time is their sum when both are measured. Inversion overhead is available in `roundtrip` mode and is calculated as `inversion time / forward time × 100`.

Inversion throughput is the number of source rows or source cells divided by inversion time. It describes the input represented by the scenario, including data that the mapping does not expose. Aggregated results report the mean, median, standard deviation, quartiles, range, outliers, and a 95% confidence interval for the mean.

CPU, RAM, swap, disk, and network measurements cover the whole host. Run comparisons without unrelated workloads.

## Saved output

Each run creates a timestamped session under `benchmarks/krown/results/`. The session contains raw results, aggregated statistics, timing plots, and system resource statistics. Interrupted runs save partial results in the same location and can be continued with `RESUME`.

See [KROWN benchmark results](krown-results.md) for published execution campaigns, their configuration, and plots.
