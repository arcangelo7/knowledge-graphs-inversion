---
# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

title: Conformance tests
description: Validation against the R2RML and RML test suites
---

The algorithm is validated against two test suites: the W3C [R2RML](https://www.w3.org/TR/r2rml/) test suite and the [RML](https://kg-construct.github.io/rml-core/spec/docs/) test suite (PostgreSQL subset). Both are included as git submodules. Forward mapping for both suites is performed with [RMLMapper](https://github.com/RMLio/rmlmapper-java) v8.0.1.

## Defining invertibility

Given a mapping M and the RDF graph G = M(D) produced by applying M to a relational instance D, the inversion function M⁻¹ attempts to reconstruct D from G. Two properties are distinguished:

- **Recoverability**: the reconstructed instance D' = M⁻¹(G) is consistent with the information that M preserves in G. Values, rows and tables that survive the forward mapping are reproduced correctly.
- **Completeness**: D' = D. Every row, column, duplicate and NULL present in D is reconstructed.

A mapping is **fully invertible** when the inversion is both recoverable and complete. It is **partially invertible** when the inversion is recoverable but incomplete, because M projects away part of D (unmapped columns or tables, NULL values in subject templates, duplicate rows collapsed by non-unique subject identifiers). It is **non-invertible** when the RDF graph does not retain enough information to reconstruct any useful approximation of D, either because all terms are constants independent of D or because the encoding is ambiguous (e.g. `rr:column` with IRI term type, where the base IRI cannot be separated from the column value).

Test cases are therefore classified into five outcomes:

| Outcome | Meaning |
|---|---|
| Fully inverted | D' = D |
| Partially inverted | D' ⊊ D with characterised loss (columns, rows, multiplicity or tables) |
| Non-invertible | Mapping does not preserve D in G (structural limitation) |
| Not supported | Engine limitation unrelated to invertibility (e.g. SQL queries as logical sources) |
| Forward mapping failed | Error test case: the forward mapping produces no RDF output |

## Setup

Initialize the submodule:

```bash
git submodule update --init --recursive
```

## Running the test suite

There are two ways to run the conformance tests: from the terminal via pytest, or through a web dashboard that provides richer feedback for debugging.

### Terminal

Pytest manages the PostgreSQL containers automatically, so no manual Docker setup is needed beyond having Docker running:

```bash
uv run pytest -v
```

To run a single test case from either suite:

```bash
uv run pytest tests/test_conformance.py::test_r2rml_conformance[R2RMLTC0001a] -v
```

```bash
uv run pytest tests/test_conformance.py::test_rml_conformance[RMLTC0001a] -v
```

To generate an HTML coverage report:

```bash
uv run pytest --cov --cov-report=html -v
```

### Web dashboard

The dashboard runs through Docker Compose and lets you run individual test cases or the full suite. For each test case it shows the generated SPARQL queries, the reconstructed SQL, and a side-by-side comparison of the original and inverted database content, which is useful when diagnosing why a particular inversion fails.

```bash
docker compose up
```

The interface is available at `http://localhost:5000`. Results are saved to `test_results/` as JSON and Markdown reports.

## W3C R2RML test suite

The [R2RML test suite](https://www.w3.org/2001/sw/rdb2rdf/test-cases/) contains 62 test cases.

| Outcome | Count |
|---|---|
| Fully inverted | 22 |
| Partially inverted | 14 |
| Non-invertible | 4 |
| Not supported | 13 |
| Forward mapping failed | 9 |

### Partially inverted (14)

Each case recovers the information preserved in the RDF graph, but the forward mapping discards part of the source. Sub-categories are counted per tag: a test may contribute to more than one sub-category when multiple forms of loss co-occur, so the counts below sum to more than the number of tests.

| Sub-category | Count |
|---|---|
| Columns lost (unmapped columns) | 10 |
| Rows lost (NULL in subject template) | 1 |
| Multiplicity lost (duplicate rows collapsed) | 4 |
| Tables lost (unmapped tables) | 1 |

One test (R2RMLTC0012a) is tagged with three sub-categories simultaneously: the `Lives` table has no triples map, `IOUs` has non-unique subject identifiers that collapse duplicates, and the column coverage check also flags unmapped columns.

### Non-invertible (4)

| Reason | Count |
|---|---|
| Constant-only mapping | 1 |
| IRI column term type (ambiguous base IRI resolution) | 3 |

### Not supported (13)

These test cases use SQL queries as logical sources (`rr:sqlQuery`), which the algorithm does not handle.

### Forward mapping failed (9)

In nine test cases the forward mapping produces no RDF output, so inversion cannot be attempted.

## RML test suite

The [RML test suite](https://kg-construct.github.io/rml-test-cases/) contains 60 PostgreSQL test cases.

| Outcome | Count |
|---|---|
| Fully inverted | 12 |
| Partially inverted | 23 |
| Non-invertible | 4 |
| Not supported | 9 |
| Forward mapping failed | 12 |

### Partially inverted (23)

Sub-categories are counted per tag; a test contributes to every form of loss that applies, so the counts below sum to more than the number of tests. Eight tests are double-tagged (`columns_lost` and `tables_lost`).

| Sub-category | Count |
|---|---|
| Columns lost (unmapped columns) | 19 |
| Tables lost (unmapped tables) | 12 |

Eleven of the twelve unmapped-tables cases are inflated by a case-sensitivity issue: the table name in the mapping (e.g. `Patient`) is compared literally against the PostgreSQL catalog name (`patient`), which lowercases unquoted identifiers. With a case-insensitive comparison these eleven would be tagged only as `columns_lost`, because each of them maps only a subset of the table's columns. The single genuine unmapped-table case is RMLTC0012a, where the `Lives` table has no triples map at all.

### Non-invertible (4)

| Reason | Count |
|---|---|
| Constant-only mapping | 1 |
| IRI column term type (ambiguous base IRI resolution) | 3 |

### Not supported (9)

All nine use SQL queries as logical sources (`rml:query`), same as the R2RML counterpart (`rr:sqlQuery`).

### Forward mapping failed (12)

In twelve test cases the forward mapping produces no RDF output, so inversion cannot be attempted.
