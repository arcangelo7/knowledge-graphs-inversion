# Knowledge Graphs Inversion

Given an RDF graph and the [R2RML](https://www.w3.org/TR/r2rml/) mapping that produced it, this tool reconstructs the original relational data. It parses the mapping document, generates SPARQL queries that reverse each mapping rule, and writes the results back as SQL statements to reconstruct the original database tables.

Full documentation at [arcangelo7.github.io/knowledge-graphs-inversion](https://arcangelo7.github.io/knowledge-graphs-inversion/).

## Quick start

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/arcangelo7/knowledge-graphs-inversion.git
```

```bash
cd knowledge-graphs-inversion && uv sync
```

```python
from kgi.core import inversion

result = inversion(config_file="morph_kgc_config.ini")
```

## License

ISC
