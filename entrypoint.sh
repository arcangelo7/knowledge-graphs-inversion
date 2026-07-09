#!/bin/bash

# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

set -e

shutdown() {
    echo "Shutting down services..."
    if command -v pkill >/dev/null 2>&1; then
        pkill -f python || true
    fi
    exit 0
}

trap shutdown SIGTERM SIGINT

case "${1:-app}" in
    "krown-benchmark")
        echo "Starting KROWN benchmark..."
        cd /app
        exec uv run python benchmarks/run_krown_benchmark.py "${@:2}"
        ;;
    "gtfs-benchmark")
        echo "Starting GTFS benchmark..."
        cd /app
        exec uv run python benchmarks/run_gtfs_benchmark.py "${@:2}"
        ;;
    "benchmark")
        echo "Use krown-benchmark or gtfs-benchmark"
        exit 64
        ;;
    "app"|*)
        echo "Starting main application..."
        cd /app
        exec uv run python app.py
        ;;
esac
