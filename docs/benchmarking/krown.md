<!--
SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>

SPDX-License-Identifier: ISC
-->

# KROWN

The [KROWN](https://github.com/kg-construct/KROWN) benchmark uses fully invertible `RawData` scenarios. Each run loads generated data into PostgreSQL, materializes RDF with RMLMapper, runs KGI inversion, and validates the reconstructed table.

The benchmark varies one parameter at a time:

| Scenario | Rows | Properties | `value_size` | Series |
| --- | ---: | ---: | ---: | --- |
| `rows_low` | 1,000 | 5 | 100 | Rows |
| `baseline` | 10,000 | 5 | 100 | Rows, properties, value size |
| `rows_high` | 50,000 | 5 | 100 | Rows |
| `properties_low` | 10,000 | 3 | 100 | Properties |
| `properties_high` | 10,000 | 8 | 100 | Properties |
| `value_size_low` | 10,000 | 5 | 50 | Value size |
| `value_size_high` | 10,000 | 5 | 150 | Value size |

## Run

```bash
make benchmark-krown I=100
```

`I` is the number of iterations.

## Results

The results below were collected with 100 iterations per scenario.

The benchmark stores seven distinct results. The same baseline result is shown in all three tables, so the results below contain nine rows: seven distinct results and two repeated views of the baseline.

### Rows

| Rows | CSV MiB | RDF triples | RMLMapper (s) | Inversion (s) | Overhead | Rows/s | Cells/s |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 0.52 | 5,000 | 1.17 ± 0.00 | 0.57 ± 0.02 | 49.1 ± 2.0% | 1,821 | 10,928 |
| 10,000 | 5.29 | 50,000 | 1.61 ± 0.01 | 0.82 ± 0.03 | 51.2 ± 1.6% | 12,440 | 74,638 |
| 50,000 | 26.69 | 250,000 | 2.98 ± 0.01 | 2.22 ± 0.02 | 74.3 ± 0.8% | 22,615 | 135,688 |

![RMLMapper and inversion times as the number of rows varies.](images/krown_rows_timing.png)

### Properties

| Properties | CSV MiB | RDF triples | RMLMapper (s) | Inversion (s) | Overhead | Rows/s | Cells/s |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3 | 3.19 | 30,000 | 1.46 ± 0.01 | 0.71 ± 0.03 | 48.8 ± 1.9% | 14,573 | 58,290 |
| 5 | 5.29 | 50,000 | 1.61 ± 0.01 | 0.82 ± 0.03 | 51.2 ± 1.6% | 12,440 | 74,638 |
| 8 | 8.43 | 80,000 | 1.78 ± 0.01 | 0.99 ± 0.02 | 55.5 ± 1.2% | 10,253 | 92,276 |

![RMLMapper and inversion times as the number of properties varies.](images/krown_properties_timing.png)

### Value size

| Value size | CSV MiB | RDF triples | RMLMapper (s) | Inversion (s) | Overhead | Rows/s | Cells/s |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 50 | 2.90 | 50,000 | 1.51 ± 0.01 | 0.75 ± 0.02 | 49.9 ± 1.3% | 13,474 | 80,844 |
| 100 | 5.29 | 50,000 | 1.61 ± 0.01 | 0.82 ± 0.03 | 51.2 ± 1.6% | 12,440 | 74,638 |
| 150 | 7.67 | 50,000 | 1.67 ± 0.01 | 0.83 ± 0.02 | 50.0 ± 1.2% | 12,172 | 73,031 |

![RMLMapper and inversion times as the value size varies.](images/krown_value_size_timing.png)
