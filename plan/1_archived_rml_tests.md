## Where the database test cases are

There are two repositories to consider:

1. **Archived repository** (https://github.com/kg-construct/rml-test-cases). This repo is marked as deprecated ("new test cases are published per module") but it remains the only source for database-specific RML test cases.
	- Test case directories spanning RMLTC0000 through RMLTC0020
2. **New modular repository** (https://github.com/kg-construct/rml-core): currently only JSON variants.
	- Contains 76 test cases spanning RMLTC0000 through RMLTC0031. Test cases RMLTC0021 through RMLTC0031 are entirely new and have no equivalent in the archived repo or in R2RML. Compared to the archived repo, the new repo also drops several test cases from the 0000-0020 range (detailed in [2_new_rml_tests.md](2_new_rml_tests.md)).

## Comparison between archived RML test cases and R2RML test cases

Our project currently uses 62 R2RML test cases (R2RMLTC0000 through R2RMLTC0020), matching the kg-construct/r2rml-test-cases-support repository. The W3C spec (https://www.w3.org/2001/sw/rdb2rdf/test-cases/) defines 63 test cases, but R2RMLTC0003a ("undefined SQL Version identifier", an error test case) was never implemented in the support repository. The archived RML repo covers the same range (RMLTC0000 through RMLTC0020) with 60 PostgreSQL test cases.

### Direct equivalents

Most RML-PostgreSQL archived test cases map directly to R2RML test cases and test the same features, with only the expected vocabulary and quoting differences (detailed in the "Systematic differences" section below). Test cases marked with * have inconsistencies. Test cases marked with ** are affected by the PostgreSQL case sensitivity bug documented below.

| Feature area                    | R2RML IDs                                   | RML IDs (archived) | Notes                                                                                                          |
| ------------------------------- | ------------------------------------------- | ------------------ | -------------------------------------------------------------------------------------------------------------- |
| Empty table                     | R2RMLTC0000                                 | RMLTC0000          |                                                                                                                |
| One column mapping              | R2RMLTC0001a                                | RMLTC0001a         |                                                                                                                |
| One column mapping              | R2RMLTC0001b                                | RMLTC0001b*        | RML tests SQL query, not blank nodes                                                                           |
| Two column variants             | R2RMLTC0002a-e, R2RMLTC0002g-h              | RMLTC0002a-e, RMLTC0002g-h |                                                                                                          |
| Two column variants             | R2RMLTC0002f                                | RMLTC0002f**       | Case sensitivity bug                                                            |
| Two column variants             | R2RMLTC0002i-j                              | RMLTC0002i-j*      | R2RML uses `rr:sqlQuery` with correct columns and expects output; RML uses `rml:query` with wrong column names (`IDs`, `NoColumnName`) and expects no output (error test cases) |
| Three column mapping            | R2RMLTC0003b-c (0003a missing from our set) | RMLTC0003a-c       |                                                                                                                |
| Multiple triples from one row   | R2RMLTC0004a                                | RMLTC0004a         |                                                                                                                |
| Multiple triples from one row   | R2RMLTC0004b                                | RMLTC0004b*        | Both have invalid `rr:termType rr:Literal` on subject map; R2RML also uses `rr:sqlQuery` RML uses simple table |
| Resource typing                 | R2RMLTC0005a                                | RMLTC0005a**       | Case sensitivity bug                                                            |
| Resource typing                 | R2RMLTC0005b                                | RMLTC0005b**       | Different blank node template separators; case sensitivity bug                  |
| Constants                       | R2RMLTC0006a                                | RMLTC0006a         |                                                                                                                |
| Named graphs and typing         | R2RMLTC0007a-f                              | RMLTC0007a-f*      | RML adds unmapped `LastName` column, making these non-invertible (R2RML maps all columns); `Name` renamed to `FirstName` |
| Named graphs and typing         | R2RMLTC0007g-h                              | RMLTC0007g-h*      | Same unmapped `LastName` issue; also missing rdf:type triple; 0007h references non-existent `Name` column      |
| Composite keys, ref object maps | R2RMLTC0008a, R2RMLTC0008c                  | RMLTC0008a, RMLTC0008c |                                                                                                             |
| Composite keys, ref object maps | R2RMLTC0008b                                | RMLTC0008b*        | morph-kgc fails to produce the RefObjectMap triple (`ex:Sport`) from the RML mapping (4 triples instead of 5); RMLMapper handles it correctly, which is one of the reasons for switching to RMLMapper |
| Foreign keys                    | R2RMLTC0009a-d                              | RMLTC0009a-d       | 0009c-d: `rr:sqlQuery` vs `rml:query`                                                                          |
| Special characters              | R2RMLTC0010a-c                              | RMLTC0010a-c       |                                                                                                                |
| Many-to-many                    | R2RMLTC0011a                                | RMLTC0011a*/**     | Both use SQL query (not supported). RML also has case sensitivity bug (unquoted identifiers in SQL script) |
| Many-to-many                    | R2RMLTC0011b                                | RMLTC0011b         |                                                                                                                |
| Blank nodes                     | R2RMLTC0012a                                | RMLTC0012a**       | Different blank node template separators (R2RML: no separator, RML: underscores); RMLMapper percent-encodes underscores as `5f` in blank node IDs (differs from expected output but forward mapping succeeds); case sensitivity bug in SQL script; non-invertible (unmapped `Lives` table) |
| Blank nodes                     | R2RMLTC0012b                                | RMLTC0012b*        | Different table names (IOUs/Lives vs persons/lives)                                                            |
| Blank nodes                     | R2RMLTC0012c-d                              | RMLTC0012c-d       |                                                                                                                |
| Blank nodes                     | R2RMLTC0012e                                | RMLTC0012e**       | Case sensitivity bug                                                            |
| Null values                     | R2RMLTC0013a                                | RMLTC0013a**       | Case sensitivity bug                                                            |
| Language tags                   | R2RMLTC0015a-b                              | RMLTC0015a-b       |                                                                                                                |
| SQL datatypes                   | R2RMLTC0016a-e                              | RMLTC0016a-e**     | Case sensitivity bug                                                            |
| CHAR type                       | R2RMLTC0018a                                | RMLTC0018a**       | Case sensitivity bug                                                            |
| IRI values                      | R2RMLTC0019a                                | RMLTC0019a         |                                                                                                                |
| IRI values                      | R2RMLTC0019b                                | RMLTC0019b**       | Case sensitivity bug                                                            |
| IRI errors                      | R2RMLTC0020a-b                              | RMLTC0020a-b**     | Case sensitivity bug                                                            |

The coverage is nearly complete. The legacy RML repo adapted R2RML features to RML vocabulary: for instance, RMLTC0009c-d use `rml:query` instead of R2RML's `rr:sqlQuery` for SQL queries as logical sources (verified in https://github.com/kg-construct/rml-test-cases/blob/master/test-cases/RMLTC0009c-PostgreSQL/mapping.ttl). Similarly, RMLTC0002f-j exist as PostgreSQL variants and test the same SQL identifier scenarios as their R2RML counterparts.

The only R2RML test cases with no equivalent in either the legacy or new RML repos are **R2RMLTC0014a-c**, which test `rr:inverseExpression`, a construct that exists only in R2RML.

Conversely, **RMLTC0003a** exists in the RML legacy repo and corresponds to R2RMLTC0003a, which is defined in the W3C spec but was never added to the r2rml-test-cases-support repository.

The RML legacy repo therefore has 60 PostgreSQL test cases: 62 - 3 (no 0014a-c) + 1 (adds 0003a) = 60.

### PostgreSQL case sensitivity bug in RML test cases

14 RML test cases fail at the forward mapping stage (morph-kgc cannot generate RDF) due to a mismatch between the SQL setup scripts and the mapping files.

The R2RML SQL scripts use quoted identifiers (`CREATE TABLE "IOUs"`), which preserves the original case in PostgreSQL. The RML SQL scripts use unquoted identifiers (`CREATE TABLE IOUs`), which PostgreSQL normalizes to lowercase (`ious`). The mapping files then reference the original mixed-case name (`rr:tableName "IOUs"`), and morph-kgc generates a quoted SQL query (`SELECT ... FROM "IOUs"`). Since the table is stored as `ious`, PostgreSQL raises `relation "IOUs" does not exist`.

## Implementation

### Test suite abstraction

Extracted a `TestSuite` base class with R2RML and RML implementations, replacing hardcoded R2RML paths and manifest queries throughout `app.py` and `test.py`. The web interface now supports suite selection and runs tests from either or both suites. MySQL support dropped in favour of PostgreSQL-only operation.

### Literal subject map validation

Literal subjects are invalid in R2RML/RML, so this validation error takes priority over the sqlQuery unsupported check. Previously, R2RMLTC0004b was classified as `UnsupportedMappingError` (sqlQuery) when the real issue is the invalid literal term type on the subject map.

### Forward mapping engine: RMLMapper

The R2RML implementation report (https://kg-construct.github.io/r2rml-implementation-report/) shows Ontop as the most spec-compliant R2RML engine on PostgreSQL (54/59 passed vs RMLMapper's 51/59). However, Ontop only supports R2RML, not RML. Since we need a single engine for both the R2RML and RML test suites, RMLMapper is the only option that covers both with reasonable conformance.

The official R2RML implementation report tested RMLMapper v4.10.0 on PostgreSQL in 2021 and found 5 failures out of 56 tests. With v8.0.1 (Docker image, 2026) we observe only 3 failures. Two have been fixed between versions:

| Test | Issue | v4.10.0 (2021) | v8.0.1 (2026) |
|------|-------|:--------------:|:-------------:|
| R2RMLTC0002f | `{ID}` (regular, should lowercase to `id`) treated same as `{\"ID\"}` (delimited, case-preserved) in templates | failed | failed |
| R2RMLTC0004b | `rr:termType rr:Literal` on subject map should be rejected as invalid | failed | passed |
| R2RMLTC0016e | `VARBINARY` column used in IRI template (`data:image/png;hex,{Photo}`) requires hex serialization of binary data | failed | passed |
| R2RMLTC0019b | `rr:column` with IRI term type: RMLMapper correctly resolves non-absolute values against `@base` and skips invalid ones (spaces), producing partial output. The spec allows this (`MAY provide partial access`) but the test expects no output | failed | failed |
| R2RMLTC0020b | Same `rr:column` + `@base` resolution with partial output. `rr:column` does not percent-encode (unlike `rr:template`), so spaces cause data errors while slashes and `..` paths produce valid IRIs | failed | failed |

R2RMLTC0002f is a genuine RMLMapper bug. R2RMLTC0019b and R2RMLTC0020b follow the spec but the test cases are stricter than what the spec mandates. None of the 3 affect inversion correctness.

### Forward mapping failure detection

Empty forward mapping output (0-byte file) is now detected before attempting inversion. Error test cases (`expected_output=False`) are skipped in pytest and shown as "forward mapping failed" in the web dashboard. The `mapping_error` category was removed and absorbed into `forward_mapping_failed`, since a mapping accepted by RMLMapper should not be rejected by the parsing layer.

### Non-invertible rr:column IRI term maps

When a term map uses `rr:column` (or a template with a single placeholder and no static prefix) with IRI term type, the original column value cannot be recovered because base IRI resolution is ambiguous: the same output IRI could come from a relative or absolute input value.

### Performance improvements

Replaced SPARQLWrapper with sparqlite (https://opencitations.github.io/sparqlite/architecture/benchmarks/) and rdflib with pyoxigraph for RDF parsing and SPARQL queries on local store. pyoxigraph enforces RFC 3986 compliance when constructing `NamedNode` instances, while rdflib's `URIRef` silently accepted malformed IRIs without any validation.

## Questions

Should we allow inversion of malformed RDF? pyoxigraph now correctly rejects these inputs, and the tool classifies them as "non-invertible". However, morph-kgc itself is permissive in both directions: it does not fully validate R2RML/RML mapping input, and it does not validate the RDF it produces as output. Invalid mappings can be processed, and the resulting RDF may contain malformed IRIs.
