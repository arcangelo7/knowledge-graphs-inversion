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

The [R2RML test suite](https://www.w3.org/2001/sw/rdb2rdf/test-cases/) contains 62 test cases.

Of these 62 cases, 16 use SQL queries as logical sources (`rr:sqlQuery`), which the algorithm does not handle. The remaining 46 break down as follows:

| Category | Count |
|---|---|
| Successfully inverted | 24 |
| Non-invertible: partial mappings | 9 |
| Non-invertible: non-unique subject templates | 3 |
| Non-invertible: invalid RDF data | 3 |
| Non-invertible: combined cases | 1 |
| Non-invertible: constant-only mapping | 1 |
| Non-invertible: NULL in subject template | 1 |
| Invalid mappings (correctly rejected) | 4 |

The 24 passing cases cover all the term map types and extraction strategies described in the [algorithm overview](/knowledge-graphs-inversion/concepts/how-it-works/). The 18 non-invertible cases each fall into one of the [known limitation categories](/knowledge-graphs-inversion/concepts/limitations/).

Four test cases contain invalid mappings (literal term type on subject maps, literals as graph names, missing subject maps, multiple subject maps per triples map) and are correctly detected and rejected.