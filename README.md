# Knowledge Graphs Inversion

[![Run tests](https://github.com/arcangelo7/knowledge-graphs-inversion/actions/workflows/test.yml/badge.svg)](https://github.com/arcangelo7/knowledge-graphs-inversion/actions/workflows/test.yml)
[![Coverage](https://arcangelo7.github.io/knowledge-graphs-inversion/coverage/coverage-badge.svg)](https://arcangelo7.github.io/knowledge-graphs-inversion/coverage/)
[![License: ISC](https://img.shields.io/badge/license-ISC-blue.svg)](https://opensource.org/licenses/ISC)
[![REUSE](https://api.reuse.software/badge/github.com/arcangelo7/knowledge-graphs-inversion)](https://api.reuse.software/info/github.com/arcangelo7/knowledge-graphs-inversion)
[![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

Given an RDF graph and the [R2RML](https://www.w3.org/TR/r2rml/) or [RML](https://kg-construct.github.io/rml-core/spec/docs/) mapping that produced it, this tool reconstructs the original relational data. It parses the mapping document, generates SPARQL queries that reverse each mapping rule, and materializes the reconstructed rows in a destination database.

The documentation explains [how inversion works](https://arcangelo7.github.io/knowledge-graphs-inversion/concepts/how-it-works/) and records the [supported scope and limits](https://arcangelo7.github.io/knowledge-graphs-inversion/concepts/limitations/).

## Quick start

Requires Python 3.11, 3.12, or 3.13 and [uv](https://docs.astral.sh/uv/). See [Installation](https://arcangelo7.github.io/knowledge-graphs-inversion/getting-started/installation/) for the full setup and [Usage](https://arcangelo7.github.io/knowledge-graphs-inversion/getting-started/usage/) for the Python API:

```bash
git clone https://github.com/arcangelo7/knowledge-graphs-inversion.git
```

```bash
cd knowledge-graphs-inversion && uv sync
```

```python
import kgi

kgi.reconstruct(
    mapping="mapping.ttl",
    rdf_graph="output.nq",
    dest_db_url="postgresql+psycopg2://user:pass@localhost:5433/dest",
    source_db_url="postgresql+psycopg2://user:pass@localhost:5432/source",
)
```

`dest_db_url` is required and identifies the database that receives the reconstructed tables. The function returns `None`. `source_db_url` is optional and is used to read the original column types and ordering. When an RML mapping contains a [D2RQ](http://d2rq.org/) database definition, its connection information is used as `source_db_url` unless an explicit value is passed. RDF queries are executed locally with PyOxigraph.

## Dashboard

Start the web interface and its databases with Docker Compose:

```bash
make submodules
docker compose up --build
```

Open [http://localhost:5000](http://localhost:5000), then choose a database and a test suite. RMLMapper maps forward and KGI inverts, on both the R2RML and the RML suite, and both tools run inside the application container. The [dashboard guide](https://arcangelo7.github.io/knowledge-graphs-inversion/validation/conformance-tests/#dashboard) covers this workflow.

## Testing

[Conformance tests](https://arcangelo7.github.io/knowledge-graphs-inversion/validation/conformance-tests/) need [Docker](https://www.docker.com/) and Java 21 or later for RMLMapper:

```bash
make test-conformance
```

## Benchmarking

Benchmark targets initialize submodules, run the needed Docker Compose services, validate completed inversions, and clean up Compose services on exit:

```bash
make benchmark-krown
```

The KROWN collector runs on the Linux host and reports system-wide CPU, memory, disk, and network activity. Avoid unrelated workloads while collecting results. The benchmark guides cover [KROWN](https://arcangelo7.github.io/knowledge-graphs-inversion/benchmarking/krown/), its [saved results](https://arcangelo7.github.io/knowledge-graphs-inversion/benchmarking/krown-results/), and [GTFS Bench](https://arcangelo7.github.io/knowledge-graphs-inversion/benchmarking/gtfs/).

## License

ISC
