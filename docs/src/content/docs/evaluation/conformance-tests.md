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

The entire test suite runs through Docker Compose, which manages the required external services (PostgreSQL databases for source and destination data):

```bash
docker compose up
```

The web interface at `http://localhost:5000` lets you run individual test cases or the full suite. Each run compares the original database content against the reconstructed output and reports whether the inversion matches.

Results are saved to the `test_results/` directory as JSON and Markdown reports.

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