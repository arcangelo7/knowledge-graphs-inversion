<!--
SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>

SPDX-License-Identifier: ISC
-->

# Conformance tests

The algorithm is validated against two test suites: the W3C [R2RML](https://www.w3.org/TR/r2rml/) test suite and the [RML](https://kg-construct.github.io/rml-core/spec/docs/) RDB test cases from a [fork of rml-io-registry](https://github.com/arcangelo7/rml-io-registry/tree/add-rdb-core-tests). Both are included as git submodules. Forward mapping for both suites is performed with [RMLMapper](https://github.com/RMLio/rmlmapper-java) v8.1.0.

## Defining invertibility

Given a mapping M and the RDF graph G = M(D) produced by applying M to a relational instance D, the inversion function M⁻¹ attempts to reconstruct D from G. Two properties are distinguished:

- **Recoverability**: the reconstructed instance D' = M⁻¹(G) is consistent with the information that M preserves in G. Values, rows and tables that survive the forward mapping are reproduced correctly.
- **Completeness**: D' = D. Every row, column, duplicate and NULL present in D is reconstructed.

A mapping is **fully invertible** when the inversion is both recoverable and complete. It is **partially invertible** when the inversion is recoverable but incomplete, either because M projects away part of D (unmapped columns or tables, NULL values in subject templates, duplicate rows collapsed by non-unique subject identifiers) or because G carries a column's values without recording which column they belong to (indistinguishable subject templates or graph maps, `rr:column` with IRI term type where the base IRI cannot be separated from the value). Such a column is left out of D' rather than filled with a value that could be wrong, so recoverability is preserved. It is **non-invertible** when no column of a table survives, either because all terms are constants independent of D or because every term map naming a column encodes it ambiguously.

Test cases are therefore classified into five outcomes:

| Outcome | Meaning |
|---|---|
| Fully inverted | D' = D |
| Partially inverted | D' ⊊ D with characterised loss (columns, rows, multiplicity or tables) |
| Non-invertible | Mapping does not preserve D in G (structural limitation) |
| Not supported | Engine limitation unrelated to invertibility (e.g. SQL queries as logical sources) |
| Forward mapping failed | Error test case: the forward mapping produces no RDF output |

## Running the test suite

Use the root Makefile entry point. Docker must be running, and Java 21 or newer must be available for RMLMapper. The RMLMapper v8.1.0 jar is downloaded automatically on the first forward mapping run:

```bash
make test-conformance
```

`DATABASE` accepts `postgresql` and `mysql`. PostgreSQL is the default and runs all 121 cases:

```bash
make test-conformance DATABASE=postgresql
```

MySQL 9.7.1 runs the 62 R2RML cases:

```bash
make test-conformance DATABASE=mysql
```

The 59 RML cases are skipped with MySQL because the RML Core RDB test suite does not yet provide MySQL variants.

To pass the conformance tests, MySQL is configured to interpret double-quoted table and column names as identifiers (`ANSI_QUOTES`). Moreover, the JDBC connection is configured to preserve trailing spaces when reading fixed-width `CHAR` values (`padCharsWithSpace=true`); this padding is required by R2RMLTC0018a.

### Dashboard

Start the dashboard and its PostgreSQL and MySQL databases with Docker Compose:

```bash
docker compose up --build
```

Open [http://localhost:5000](http://localhost:5000), then choose the database and test suite.

## W3C R2RML test suite

The [R2RML test suite](https://www.w3.org/2001/sw/rdb2rdf/test-cases/) contains 62 test cases.

| Outcome | PostgreSQL | MySQL |
|---|---:|---:|
| Fully inverted | 22 | 22 |
| Partially inverted | 15 | 15 |
| Non-invertible | 3 | 3 |
| Not supported | 13 | 14 |
| Forward mapping failed | 9 | 8 |

### Partially inverted (15)

Each case recovers the information preserved in the RDF graph, but the forward mapping discards part of the source. Sub-categories are counted per tag: a test may contribute to more than one sub-category when multiple forms of loss co-occur, so the counts below sum to more than the number of tests.

| Sub-category | PostgreSQL | MySQL |
|---|---:|---:|
| Columns lost (unmapped or unassignable columns) | 11 | 11 |
| Rows lost (NULL in subject template) | 1 | 1 |
| Multiplicity lost (duplicate rows collapsed) | 4 | 4 |
| Tables lost (unmapped tables) | 1 | 1 |

One test (R2RMLTC0012a) is tagged with three sub-categories simultaneously: the `Lives` table has no triples map, `IOUs` has non-unique subject identifiers that collapse duplicates, and the column coverage check also flags unmapped columns.

### Non-invertible (3)

| Reason | PostgreSQL | MySQL |
|---|---:|---:|
| Constant-only mapping | 1 | 1 |
| Sole column reference has an IRI term type (ambiguous base IRI resolution) | 2 | 2 |

R2RMLTC0020a and R2RMLTC0020b map a single-column table through a subject map with an IRI term type over that column. The column is unassignable and no other term map names one, so nothing remains to reconstruct.

### Not supported

These test cases use SQL queries as logical sources (`rr:sqlQuery`), which the algorithm does not handle.

The difference concerns R2RMLTC0002h, which stops at different stages of the test pipeline. With PostgreSQL, forward mapping produces no RDF, so inversion cannot be attempted. With MySQL, RDF is produced and inversion proceeds until it encounters the unsupported SQL query. The databases therefore provide the same inversion support; only the stage at which this test stops changes.

### Forward mapping failed

The forward mapping produces no RDF output for nine PostgreSQL cases and eight MySQL cases, so inversion cannot be attempted. R2RMLTC0002h accounts for the difference.

## RML test suite

The RML test suite comes from a [fork of rml-io-registry](https://github.com/arcangelo7/rml-io-registry/tree/add-rdb-core-tests) and contains 59 RDB test cases.

| Outcome | Count |
|---|---|
| Fully inverted | 14 |
| Partially inverted | 22 |
| Non-invertible | 2 |
| Not supported | 11 |
| Forward mapping failed | 10 |

### Partially inverted (22)

Sub-categories are counted per tag; a test contributes to every form of loss that applies, so the counts below sum to more than the number of tests.

| Sub-category | Count |
|---|---|
| Columns lost (unmapped or unassignable columns) | 18 |
| Rows lost (NULL in subject template) | 1 |
| Multiplicity lost (duplicate rows collapsed) | 4 |
| Tables lost (unmapped tables) | 1 |

One test (RMLTC0012a) is tagged with three sub-categories simultaneously, mirroring its R2RML counterpart: the `Lives` table has no triples map, `IOUs` has non-unique subject identifiers that collapse duplicates, and the column coverage check also flags unmapped columns.

Test cases sharing an identifier across the two suites are not always equivalent: the RML-Core suite sometimes changed the source data. The seven RMLTC0007 variants use a `student` table with an extra `LastName` column that no term map references. This is the same data as their [RMLTC0007-JSON counterparts](https://github.com/kg-construct/rml-core/tree/main/test-cases) in the RML-Core suite, so they classify as partially inverted (columns lost), whereas the R2RML 0007 tests map every column and are fully inverted.

Another example is 0019b (subject map from a column whose value is not a valid IRI): the RML variant keeps only the invalid row, RMLMapper produces no output and the test classifies as forward mapping failed, while the R2RML variant also contains valid rows for which RMLMapper emits triples. The inversion is therefore attempted: the subject IRIs are unassignable, but an object map exposes the same column, so the R2RML variant classifies as partially inverted.

A column can also be lost through a join condition. RMLTC0021a (an RML-Core addition with no R2RML counterpart) joins the `student` table with itself on the `Sport` column, whose values never appear in the generated RDF: the graph records which students share a sport, but not which sport. A join condition equates its child and parent columns, so a join column counts as covered only when the column on the other side is emitted by a term map; here neither side is, so `Sport` is counted as lost.

### Non-invertible (2)

| Reason | Count |
|---|---|
| Constant-only mapping | 1 |
| Sole column reference has an IRI term type (ambiguous base IRI resolution) | 1 |

### Not supported (11)

All eleven use SQL queries as logical sources (`rml:referenceFormulation rml:SQL2008Query`), same as the R2RML counterpart (`rr:sqlQuery`).

### Forward mapping failed (10)

All ten are intentional error test cases: the mapping or the data contains the error under test, RMLMapper produces no RDF output, and inversion cannot be attempted.
