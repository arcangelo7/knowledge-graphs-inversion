<!--
SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>

SPDX-License-Identifier: ISC
-->

# Conformance tests

The R2RML run contains the 62 W3C [R2RML](https://www.w3.org/TR/r2rml/) cases followed by ten local cases that isolate structural inversion limits. The local catalog follows the W3C manifest and asset layout but uses `INVTC` identifiers, so it remains outside the official numbering. The RML run contains 59 RDB cases from a [fork of rml-io-registry](https://github.com/arcangelo7/rml-io-registry/tree/add-rdb-core-tests), while the two official catalogs are included as git submodules. Forward mapping uses [RMLMapper](https://github.com/RMLio/rmlmapper-java) v8.1.0 and inversion uses KGI.

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

Use the root Makefile entry point, with Docker running and Java 21 or newer available for RMLMapper. The RMLMapper v8.1.0 jar is downloaded automatically on the first forward mapping run:

```bash
make test-conformance
```

`DATABASE` accepts `postgresql` and `mysql`. PostgreSQL is the default and runs 131 catalog cases: 72 R2RML cases and 59 RML cases:

```bash
make test-conformance DATABASE=postgresql
```

MySQL 9.7.1 runs 70 R2RML cases:

```bash
make test-conformance DATABASE=mysql
```

`R2RMLTC0002f` and `R2RMLTC0018a` run only with PostgreSQL, while the ten `INVTC` cases run on both databases. The 59 RML cases are skipped with MySQL because the RML Core RDB test suite does not yet provide MySQL variants.

### Dashboard

Start the dashboard and its PostgreSQL and MySQL databases with Docker Compose, then open [http://localhost:5000](http://localhost:5000) and choose the database and the test suite:

```bash
make submodules
docker compose up --build
```

## W3C R2RML test suite

The [R2RML test suite](https://www.w3.org/2001/sw/rdb2rdf/test-cases/) contains 62 test cases.

| Outcome | PostgreSQL | MySQL |
|---|---:|---:|
| Fully inverted | 21 | 20 |
| Partially inverted | 14 | 14 |
| Non-invertible | 2 | 2 |
| Not supported | 13 | 13 |
| Error test case | 12 | 11 |
| Mismatch | 0 | 0 |
| Not tested | 0 | 2 |

### Partially inverted (14)

Each case recovers the information preserved in the RDF graph, but the forward mapping discards part of the source, while sub-categories are counted per tag. A test may contribute to more than one sub-category when multiple forms of loss co-occur, so the counts below sum to more than the number of tests.

| Sub-category | PostgreSQL | MySQL |
|---|---:|---:|
| Columns lost (unmapped or unassignable columns) | 9 | 9 |
| Rows lost (NULL in subject template) | 1 | 1 |
| Multiplicity lost (duplicate rows collapsed) | 5 | 5 |
| Tables lost (unmapped tables) | 1 | 1 |


### Non-invertible (2)

| Reason | PostgreSQL | MySQL |
|---|---:|---:|
| Constant-only mapping | 1 | 1 |
| Sole column reference has an IRI term type (ambiguous base IRI resolution) | 1 | 1 |

R2RMLTC0020a maps a single-column table through a subject map with an IRI term type over that column. The column is unassignable and no other term map names one, so nothing remains to reconstruct.

## Inversion limit test cases

| Structural limit | Observable cases | Expected outcome |
|---|---|---|
| Unmapped column | `R2RMLTC0008c`, `R2RMLTC0010a`, `R2RMLTC0010b`, `R2RMLTC0012b`, `R2RMLTC0016a`, `R2RMLTC0016b`, `R2RMLTC0016c`, `R2RMLTC0016d`, `R2RMLTC0016e` | Partially inverted: columns lost |
| Unmapped table | `R2RMLTC0012a` | Partially inverted: tables lost |
| NULL in a subject template | `R2RMLTC0013a` | Partially inverted: rows lost |
| Duplicate rows collapsed in RDF | `R2RMLTC0005a`, `R2RMLTC0005b`, `R2RMLTC0012a`, `R2RMLTC0012b`, `R2RMLTC0012e` | Partially inverted: multiplicity lost |
| Constant-only mapping | `R2RMLTC0006a` | Non-invertible |
| Single-placeholder IRI template | `R2RMLTC0020a` | Non-invertible |
| Adjacent template placeholders, with no term map of the same table naming those columns | `INVTC0001a`, `R2RMLTC0012b` | Partially inverted: columns lost |
| Template separator present in an observed value | `INVTC0001b` | Partially inverted: columns lost |
| Indistinguishable subject maps | `INVTC0002a` | Partially inverted: columns lost |
| Indistinguishable predicate maps | `INVTC0002b` | Partially inverted: columns lost |
| Indistinguishable object maps | `INVTC0002c` | Partially inverted: columns lost |
| Indistinguishable graph maps | `INVTC0002d` | Partially inverted: columns lost |
| Column-valued IRI term map | `INVTC0003` | Partially inverted: columns lost |
| Join key absent from every RDF term | `INVTC0004` | Partially inverted: columns lost |
| Parent Triples Map reached only through a join | `INVTC0005a` | Partially inverted: columns lost |
| Triples Map without a predicate-object map, subject class, or incoming join | `INVTC0005b` | Non-invertible |

## RML test suite

The RML test suite comes from a [fork of rml-io-registry](https://github.com/arcangelo7/rml-io-registry/tree/add-rdb-core-tests) and contains 59 RDB test cases.

| Outcome | Count |
|---|---|
| Fully inverted | 13 |
| Partially inverted | 22 |
| Non-invertible | 2 |
| Not supported | 10 |
| Error test case | 12 |
| Mismatch | 0 |

### Partially inverted (22)

Sub-categories are counted per tag; a test contributes to every form of loss that applies, so the counts below sum to more than the number of tests.

| Sub-category | Count |
|---|---|
| Columns lost (unmapped or unassignable columns) | 17 |
| Rows lost (NULL in subject template) | 1 |
| Multiplicity lost (duplicate rows collapsed) | 5 |
| Tables lost (unmapped tables) | 1 |

Test cases sharing an identifier across the two suites are not always equivalent: the RML-Core suite sometimes changed the source data. The seven RMLTC0007 variants use a `student` table with an extra `LastName` column that no term map references. This is the same data as their [RMLTC0007-JSON counterparts](https://github.com/kg-construct/rml-core/tree/main/test-cases) in the RML-Core suite, so they classify as partially inverted (columns lost), whereas the R2RML 0007 tests map every column and are fully inverted.

A column can also be lost through a join condition. RMLTC0021a (an RML-Core addition with no R2RML counterpart) joins the `student` table with itself on the `Sport` column, whose values never appear in the generated RDF: the graph records which students share a sport, but not which sport. A join condition equates its child and parent columns, so a join column counts as covered only when the column on the other side is emitted by a term map; here neither side is, so `Sport` is counted as lost.

### Non-invertible (2)

| Reason | Count |
|---|---|
| Constant-only mapping | 1 |
| Sole column reference has an IRI term type (ambiguous base IRI resolution) | 1 |
