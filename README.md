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

- Python 3.12
- Docker

## Quick Start

```bash
# Clone the repository
git clone https://github.com/arcangelo7/knowledge-graphs-inversion.git
cd knowledge-graphs-inversion

docker compose up

# Access the web interface at http://localhost:5000
```

## Benchmarking

This project integrates the [KROWN benchmark framework](https://github.com/kg-construct/KROWN) for evaluating the performance of the knowledge graphs inversion system with PostgreSQL focus.

### Setup Benchmark Environment

**Initialize KROWN submodule:**
```bash
git submodule update --init --recursive
```

### Running KROWN Benchmark

**Run with Docker:**
```bash
# Start all services and run benchmark
docker compose -f docker-compose.benchmark.yml up

# Run with multiple iterations for statistical analysis (plots auto-generated)
docker compose -f docker-compose.benchmark.yml run benchmark benchmark --iterations 10

# Run without Virtuoso (in-memory RDF)
docker compose -f docker-compose.benchmark.yml run benchmark benchmark --no-virtuoso

# Stop all services
docker compose -f docker-compose.benchmark.yml down
```

### Benchmark Results

Results are stored in `benchmarks/krown/results/` with:
- Execution times for each scenario
- Statistical metrics when using `--iterations > 1`:
  - Mean, median, standard deviation, 95% confidence intervals
  - Outlier detection and percentiles
  - Box plots automatically generated (PNG files)
- Data and mapping file sizes
- Triple Maps and Predicate Object Maps counts
- JSON format for analysis

## License

ISC License

## Author

**arcangelo7** - arcangelo.massari@unibo.it