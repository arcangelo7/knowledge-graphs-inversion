---
# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

title: Conformance tests
description: Validation against the R2RML and RML test suites.
---

The algorithm is validated against two test suites: the W3C [R2RML](https://www.w3.org/TR/r2rml/) test suite and the [RML](https://kg-construct.github.io/rml-core/spec/docs/) test suite (PostgreSQL subset). Both are included as git submodules.

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

The [R2RML test suite](https://www.w3.org/2001/sw/rdb2rdf/test-cases/) contains 62 test cases, broken down as follows:

| Category | Count |
|---|---|
| Successfully inverted | 22 |
| Not supported | 13 |
| Non-invertible | 18 |
| Forward mapping failed (error test cases) | 9 |

### Successfully inverted (22)

The 22 passing cases cover all the term map types and extraction strategies described in the [algorithm overview](/knowledge-graphs-inversion/concepts/how-it-works/).

### Not supported (13)

These test cases use SQL queries as logical sources (`rr:sqlQuery`), which the algorithm does not handle.

### Non-invertible (18)

Each of these falls into one of the [known limitation categories](/knowledge-graphs-inversion/concepts/limitations/):

| Reason | Count |
|---|---|
| Partial mappings (unmapped columns) | 8 |
| Non-unique subject templates (duplicate rows lost) | 3 |
| IRI column term type (ambiguous base IRI resolution) | 3 |
| Combined causes (unmapped tables/columns and duplicates) | 2 |
| Constant-only mapping | 1 |
| NULL values in subject template | 1 |

### Forward mapping failed (9)

Nine test cases are error test cases where the R2RML forward mapping itself produces no output.