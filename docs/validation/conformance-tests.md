<!--
SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>

SPDX-License-Identifier: ISC
-->

# Conformance tests

The algorithm is validated against two test suites: the W3C [R2RML](https://www.w3.org/TR/r2rml/) test suite and the [RML](https://kg-construct.github.io/rml-core/spec/docs/) RDB test cases from a [fork of rml-io-registry](https://github.com/arcangelo7/rml-io-registry/tree/add-rdb-core-tests). Both are included as git submodules. The default path performs forward mapping with [RMLMapper](https://github.com/RMLio/rmlmapper-java) v8.1.0 and inversion with KGI. A separate R2RML path uses Soufflé in both directions.

## Defining invertibility

Given a mapping M and the RDF graph G = M(D) produced by applying M to a relational instance D, the inversion function M⁻¹ attempts to reconstruct D from G. Two properties are distinguished:

- **Recoverability**: the reconstructed instance D' = M⁻¹(G) is consistent with the information that M preserves in G. Values, rows and tables that survive the forward mapping are reproduced correctly.
- **Completeness**: D' = D. Every row, column, duplicate and NULL present in D is reconstructed.

Test cases are therefore classified into these outcomes:

| Outcome | Meaning |
|---|---|
| Fully inverted | D' = D |
| Partially inverted | D' ⊊ D with characterised loss (columns, rows, multiplicity or tables) |
| Non-invertible | Mapping does not preserve D in G (structural limitation) |
| Not supported | Engine limitation unrelated to invertibility (e.g. SQL queries as logical sources) |
| Error test case | The specification requires the mapping to stop, so no graph may exist to invert |
| Not tested | The case does not run on the selected database |
| Mismatch | D' differs from D and the difference has no characterised cause |
| Execution error | The run stopped before it could classify the case |

## Running the test suite

Use the root Makefile entry point. Docker must be running, and Java 21 or newer must be available for RMLMapper. The RMLMapper v8.1.0 jar is downloaded automatically on the first forward mapping run:

```bash
make test-conformance
```

`DATABASE` accepts `postgresql` and `mysql`. PostgreSQL is the default and runs all 121 cases:

```bash
make test-conformance DATABASE=postgresql
```

MySQL 9.7.1 runs 60 R2RML cases:

```bash
make test-conformance DATABASE=mysql
```

`R2RMLTC0002f` and `R2RMLTC0018a` run only with PostgreSQL for both execution pairs. The 59 RML cases are skipped with MySQL because the RML Core RDB test suite does not yet provide MySQL variants.

Soufflé supports only R2RML and runs each case with and without provenance:

```bash
make test-conformance FORWARD_ENGINE=souffle INVERSION_ENGINE=souffle DATABASE=postgresql
```

### Dashboard

Start the dashboard and its PostgreSQL and MySQL databases with Docker Compose:

```bash
git submodule update --init --recursive
docker compose up --build
```

Open [http://localhost:5000](http://localhost:5000), then choose the execution pair, database, and test suite. RMLMapper/KGI supports both suites, while Soufflé/Soufflé enables R2RML only.

## W3C R2RML test suite

The [R2RML test suite](https://www.w3.org/2001/sw/rdb2rdf/test-cases/) contains 62 test cases.
The outcome table below describes the RMLMapper/KGI pair.

| Outcome | PostgreSQL | MySQL |
|---|---:|---:|
| Fully inverted | 21 | 20 |
| Partially inverted | 13 | 13 |
| Non-invertible | 2 | 2 |
| Not supported | 13 | 13 |
| Error test case | 12 | 11 |
| Mismatch | 1 | 1 |
| Not tested | 0 | 2 |

### Partially inverted (13)

Each case recovers the information preserved in the RDF graph, but the forward mapping discards part of the source. Sub-categories are counted per tag: a test may contribute to more than one sub-category when multiple forms of loss co-occur, so the counts below sum to more than the number of tests.

| Sub-category | PostgreSQL | MySQL |
|---|---:|---:|
| Columns lost (unmapped or unassignable columns) | 8 | 8 |
| Rows lost (NULL in subject template) | 1 | 1 |
| Multiplicity lost (duplicate rows collapsed) | 4 | 4 |
| Tables lost (unmapped tables) | 1 | 1 |

One test (R2RMLTC0012a) is tagged with two sub-categories at once: the `Lives` table has no triples map, and `IOUs` has non-unique subject identifiers that collapse duplicates.

### Non-invertible (2)

| Reason | PostgreSQL | MySQL |
|---|---:|---:|
| Constant-only mapping | 1 | 1 |
| Sole column reference has an IRI term type (ambiguous base IRI resolution) | 1 | 1 |

R2RMLTC0020a maps a single-column table through a subject map with an IRI term type over that column. The column is unassignable and no other term map names one, so nothing remains to reconstruct.

### Mismatch (1)

R2RMLTC0012b is the only case that reconstructs values differing from the source, so its inversion is neither complete nor recoverable. It builds the blank node label of the `Lives` table from the template `{fname}{lname}`, and because the two placeholders are adjacent the label `BobSmith` carries no boundary between them, so the reconstruction assigns the whole label to `lname` and leaves `fname` empty.

### Soufflé/Soufflé

The RMLMapper/KGI outcomes above are the reference for the Soufflé pair, which runs every case twice, once from the RDF facts alone and once with provenance. Soufflé is still under development, so the columns below record where each mode stands today rather than a target.

| Outcome | PostgreSQL, RDF | PostgreSQL, provenance | MySQL, RDF | MySQL, provenance |
|---|---:|---:|---:|---:|
| Fully inverted | 14 | 19 | 14 | 18 |
| Partially inverted | 1 | 3 | 1 | 3 |
| Non-invertible | 2 | 2 | 2 | 2 |
| Not supported | 13 | 13 | 13 | 13 |
| Error test case | 12 | 12 | 11 | 11 |
| Mismatch | 20 | 13 | 19 | 13 |
| Not tested | 0 | 0 | 2 | 2 |

| Sub-category | PostgreSQL, RDF | PostgreSQL, provenance | MySQL, RDF | MySQL, provenance |
|---|---:|---:|---:|---:|
| Rows lost | 1 | 1 | 1 | 1 |
| Multiplicity lost | 0 | 2 | 0 | 2 |
| Tables lost | 0 | 1 | 0 | 1 |

Soufflé stops on every error test case, including the three that RMLMapper accepts, so the two pairs report the same count for that class. Where KGI records columns lost, Soufflé rebuilds a different set of columns, so those cases count as mismatches instead.

## RML test suite

The RML test suite comes from a [fork of rml-io-registry](https://github.com/arcangelo7/rml-io-registry/tree/add-rdb-core-tests) and contains 59 RDB test cases.

| Outcome | Count |
|---|---|
| Fully inverted | 13 |
| Partially inverted | 21 |
| Non-invertible | 2 |
| Not supported | 10 |
| Error test case | 12 |
| Mismatch | 1 |

### Partially inverted (21)

Sub-categories are counted per tag; a test contributes to every form of loss that applies, so the counts below sum to more than the number of tests.

| Sub-category | Count |
|---|---|
| Columns lost (unmapped or unassignable columns) | 16 |
| Rows lost (NULL in subject template) | 1 |
| Multiplicity lost (duplicate rows collapsed) | 4 |
| Tables lost (unmapped tables) | 1 |

One test (RMLTC0012a) is tagged with two sub-categories at once, mirroring its R2RML counterpart: the `Lives` table has no triples map, and `IOUs` has non-unique subject identifiers that collapse duplicates.

Test cases sharing an identifier across the two suites are not always equivalent: the RML-Core suite sometimes changed the source data. The seven RMLTC0007 variants use a `student` table with an extra `LastName` column that no term map references. This is the same data as their [RMLTC0007-JSON counterparts](https://github.com/kg-construct/rml-core/tree/main/test-cases) in the RML-Core suite, so they classify as partially inverted (columns lost), whereas the R2RML 0007 tests map every column and are fully inverted.

A column can also be lost through a join condition. RMLTC0021a (an RML-Core addition with no R2RML counterpart) joins the `student` table with itself on the `Sport` column, whose values never appear in the generated RDF: the graph records which students share a sport, but not which sport. A join condition equates its child and parent columns, so a join column counts as covered only when the column on the other side is emitted by a term map; here neither side is, so `Sport` is counted as lost.

### Non-invertible (2)

| Reason | Count |
|---|---|
| Constant-only mapping | 1 |
| Sole column reference has an IRI term type (ambiguous base IRI resolution) | 1 |

### Mismatch (1)

RMLTC0012b-RDB reproduces the defect described for its R2RML counterpart: the label built from two adjacent template placeholders cannot be split back into `fname` and `lname`.
