# RML Inversion

A tool for **RML inversion**: converting RDF knowledge graphs back to their original data formats (CSV, SQL) by reversing the RML mapping process.

## Overview

This project implements the inverse process of RML (RDF Mapping Language):
- **Forward RML**: CSV/SQL → RDF using morph-kgc
- **Inverse RML**: RDF → CSV/SQL (this project)

Currently supports:
- **CSV files** 
- **SQL databases**

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager

## Quick Start

```bash
# Install uv if needed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Setup project
git clone https://github.com/arcangelo7/knowledge-graphs-inversion.git
cd knowledge-graphs-inversion
uv sync

# Run the main application
uv run python app.py
```

## Managing Dependencies with uv

```bash
# Add new dependency
uv add package-name

# Remove dependency  
uv remove package-name

# Update all
uv sync --upgrade

# Run without activating venv
uv run python script.py
```

## Project Status

🚧 **In Development** - Focus on CSV and SQL format inversion

## License

ISC License

## Author

**arcangelo7** - arcangelo.massari@unibo.it
