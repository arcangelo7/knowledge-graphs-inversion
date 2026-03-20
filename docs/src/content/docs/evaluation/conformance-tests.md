---
title: Conformance tests
description: Validation against the W3C R2RML test suite.
---

The algorithm is validated against the W3C [R2RML](https://www.w3.org/TR/r2rml/) test suite. The test cases are included as a [git submodule](https://github.com/kg-construct/r2rml-test-cases-support).

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

To run a single test case:

```bash
uv run pytest tests/test_conformance.py::test_r2rml_conformance[R2RMLTC0001a] -v
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

Of these 62 cases, 17 use SQL queries as logical sources (`rr:sqlQuery`), which the algorithm does not handle. The remaining 45 break down as follows:

| Category | Count |
|---|---|
| Successfully inverted | 26 |
| Non-invertible: partial mappings | 9 |
| Non-invertible: non-unique subject templates | 3 |
| Non-invertible: combined cases | 2 |
| Non-invertible: constant-only mapping | 1 |
| Non-invertible: NULL in subject template | 1 |
| Invalid mappings (correctly rejected) | 3 |

The 26 passing cases cover all the term map types and extraction strategies described in the [algorithm overview](/knowledge-graphs-inversion/concepts/how-it-works/). The 16 non-invertible cases each fall into one of the [known limitation categories](/knowledge-graphs-inversion/concepts/limitations/).

Three test cases contain invalid mappings (literals as graph names, missing subject maps, multiple subject maps per triples map) and are correctly detected and rejected.