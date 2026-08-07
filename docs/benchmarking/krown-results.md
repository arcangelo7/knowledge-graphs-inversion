<!--
SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>

SPDX-License-Identifier: ISC
-->

# KROWN benchmark results

This page records published KROWN benchmark campaigns. Each dated section fixes the configuration used for that execution and keeps its measurements separate from the [benchmark guide](krown.md).

## 4 August 2026

This campaign measured all 58 scenarios in `roundtrip` mode with three iterations per scenario.

| Setting | Value |
| --- | --- |
| KROWN revision | [`824438c`](https://github.com/kg-construct/KROWN/commit/824438c6cc33bc7bb78a05d8ba4c16af4d4aba5e) |
| Suites | `raw`, `mappings`, `named-graphs`, `joins` |
| Forward engine | RMLMapper 8.1.0 |
| Inversion engine | `kgi`, using PyOxigraph |
| Sampling interval | 0.1 seconds |
| Metric scope | Whole system |
| Operating system | Debian GNU/Linux 12 (bookworm), Linux 6.1.0-44-amd64, x86-64 |
| CPU | 29 vCPUs, AMD EPYC 7413 24-Core Processor |
| Memory | 295 GiB RAM, no swap |
| Benchmark storage | 4 TiB ext4 virtual disk |
| PostgreSQL | 14.5 for forward materialization; 13 for inversion |

The [raw results](results/krown_benchmark_results_raw_2026-08-04.json) contain every completed iteration and recorded failure. The [aggregated statistics](results/krown_benchmark_results_stats_2026-08-04.json) contain per-scenario summaries and 95% confidence intervals.

### Outcomes and resource limits

| Outcome | Scenarios |
| --- | ---: |
| `AMBIGUOUS` | 22 |
| `PARTIAL` | 18 |
| `FULL` | 8 |
| `OUT_OF_MEMORY` | 7 |
| `TIMEOUT` | 3 |

Forward materialization completed for 48 scenarios, and every completed graph was inverted and validated. No scenario that completed an RDF round trip produced a mismatch, and no scenario returned `NON_INVERTIBLE`. The eight `FULL` outcomes came from completed `RawData` scenarios, whose mappings expose every source column.

The RMLMapper JVM limited its heap to 50% of the memory visible to Java, and each materialization had a 10,800-second limit. Seven scenarios exhausted memory: the 10-million-row case, the cases with cell sizes of 5,000 and 10,000 characters, and the four cases with 15 Named Graphs. The scenarios with 5, 10, and 15 join conditions reached the time limit. The [KROWN 0.9.0 results](https://zenodo.org/records/10973892) provide the corresponding reference campaign.

### Inversion overhead

Inversion overhead is `inversion time / forward time × 100`. The ranges below compare the mean overhead of each completed scenario in a suite.

| Suite | Scenarios measured | Mean overhead range |
| --- | ---: | ---: |
| Raw data | 8 | 48%–384% |
| Mappings | 7 | 105%–318% |
| Named Graphs | 20 | 124%–543% |
| Joins | 13 | 0.2%–0.3% |

The joins ratio reflects the long forward phase. Mean materialization times ranged from 6,058 to 7,499 seconds, while mean inversion times ranged from 12 to 25 seconds. The resulting overhead was below 0.4% in every completed join scenario.

### Raw data

![RMLMapper and inversion times as the number of rows varies.](images/krown_rows_timing.png)

![RMLMapper and inversion times as the number of properties varies.](images/krown_properties_timing.png)

![RMLMapper and inversion times as the value size varies.](images/krown_value_size_timing.png)

### Mappings

![RMLMapper and inversion times as the number of Triples Maps varies.](images/krown_mappings_triples_maps_timing.png)

![RMLMapper and inversion times as the number of Predicate-Object Maps varies.](images/krown_mappings_predicate_object_maps_timing.png)

### Named Graphs

![RMLMapper and inversion times for static Named Graphs in the Subject Map.](images/krown_named_graphs_subject_static_timing.png)

![RMLMapper and inversion times for dynamic Named Graphs in the Subject Map.](images/krown_named_graphs_subject_dynamic_timing.png)

![RMLMapper and inversion times for static Named Graphs in Predicate-Object Maps.](images/krown_named_graphs_pom_static_timing.png)

![RMLMapper and inversion times for dynamic Named Graphs in Predicate-Object Maps.](images/krown_named_graphs_pom_dynamic_timing.png)

![RMLMapper and inversion times for static Named Graphs in Subject and Predicate-Object Maps.](images/krown_named_graphs_both_static_timing.png)

![RMLMapper and inversion times for dynamic Named Graphs in Subject and Predicate-Object Maps.](images/krown_named_graphs_both_dynamic_timing.png)

### Joins

![RMLMapper and inversion times for one-to-many relations.](images/krown_joins_one_to_many_timing.png)

![RMLMapper and inversion times for many-to-one relations.](images/krown_joins_many_to_one_timing.png)

![RMLMapper and inversion times for many-to-many relations.](images/krown_joins_many_to_many_timing.png)

![RMLMapper and inversion times as the number of join conditions varies.](images/krown_joins_conditions_timing.png)
