# CHANGELOG

<!-- version list -->

## v1.2.0 (2026-02-25)

### Bug Fixes

- Resolve Pyright type errors across kgi package
  ([`03a37cd`](https://github.com/arcangelo7/knowledge-graphs-inversion/commit/03a37cddca364527fb1b599933213bb03ffa1be9))

- **test**: Improve R2RML test result classification accuracy
  ([`a5dcd12`](https://github.com/arcangelo7/knowledge-graphs-inversion/commit/a5dcd125547ee6f1133c94c50000d3929380fc67))

### Chores

- Trigger release [release]
  ([`2d44008`](https://github.com/arcangelo7/knowledge-graphs-inversion/commit/2d4400861ab847b24a01a9cce97f7667530997c6))

### Features

- **inversion**: Reconstruct foreign key columns from RefObjectMap join conditions
  ([`d182dd3`](https://github.com/arcangelo7/knowledge-graphs-inversion/commit/d182dd3b9c6e1460676e2159fcc86c33d3bdddfe))

### Performance Improvements

- **query**: Skip subject template extraction when all references are already bound
  ([`731ff3e`](https://github.com/arcangelo7/knowledge-graphs-inversion/commit/731ff3e37339b8fbeeae5c93d4ac0f66e615d557))

### Refactoring

- **query**: Simplify SPARQL variable binding by removing encoded suffix pattern
  ([`2b65c2c`](https://github.com/arcangelo7/knowledge-graphs-inversion/commit/2b65c2c48facfd1cbe25281fbef018ed8fba69d7))

- **query**: Simplify SPARQL variable naming and eliminate redundant BINDs
  ([`bc9f470`](https://github.com/arcangelo7/knowledge-graphs-inversion/commit/bc9f4708e8bce76967882c633ec56ca974537ad3))


## v1.1.0 (2025-10-26)

### Features

- **benchmark**: Add statistical analysis and plotting for multiple iterations
  ([`504ff7f`](https://github.com/arcangelo7/knowledge-graphs-inversion/commit/504ff7f8fdde9cdcbc840419f407cde834b5986d))

- **benchmark**: Add timing breakdown and overhead metrics to KROWN benchmark
  ([`2bc3d4c`](https://github.com/arcangelo7/knowledge-graphs-inversion/commit/2bc3d4c27e62937a07f81933efc2d371b2617023))

- **benchmark**: Improve outlier display with adaptive precision grouping [release]
  ([`ec0113b`](https://github.com/arcangelo7/knowledge-graphs-inversion/commit/ec0113b045304c5c862155076d1c5da1fbef03d2))

- **benchmark**: Improve plot layout and statistics display [release]
  ([`7a3c26f`](https://github.com/arcangelo7/knowledge-graphs-inversion/commit/7a3c26f555d96ba938afbb2a9a714daa1c4a69a4))


## v1.0.0 (2025-10-23)

- Initial Release
