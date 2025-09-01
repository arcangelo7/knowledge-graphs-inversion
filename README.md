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

## Benchmarking

This project integrates the [KROWN benchmark framework](https://github.com/kg-construct/KROWN) for evaluating the performance of the knowledge graphs inversion system with PostgreSQL focus.

### Setup Benchmark Environment

1. **Initialize KROWN submodule:**
   ```bash
   git submodule update --init --recursive
   ```

2. **Install dependencies:**
   ```bash
   uv sync
   ```

### Running KROWN Benchmark

**Run with Docker:**
```bash
# Start all services and run benchmark
docker compose -f docker-compose.benchmark.yml up

# Run with validation
docker compose -f docker-compose.benchmark.yml run benchmark --validate

# Run without Virtuoso (in-memory RDF)
docker compose -f docker-compose.benchmark.yml run benchmark --no-virtuoso

# Stop all services
docker compose -f docker-compose.benchmark.yml down
```

### Benchmark Results

Results are stored in `benchmarks/krown/results/` with:
- Execution times for each scenario
- Data and mapping file sizes
- Triple Maps and Predicate Object Maps counts
- JSON format for analysis

## License

ISC License

## Author

**arcangelo7** - arcangelo.massari@unibo.it
