#!/bin/bash

# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

set -e

shutdown() {
    echo "Shutting down services..."
    if command -v pkill >/dev/null 2>&1; then
        pkill -f qlever-server || true
        pkill -f python || true
    fi
    exit 0
}

trap shutdown SIGTERM SIGINT

case "${1:-app}" in
    "benchmark")
        echo "Starting benchmark..."
        cd /app
        exec uv run python benchmarks/run_krown_benchmark.py "${@:2}"
        ;;
    "app"|*)
        echo "Starting main application..."
        cd /app
        exec uv run python app.py
        ;;
esac
