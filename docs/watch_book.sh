#!/bin/bash

# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

set -euo pipefail

echo "Starting Jupyter Book watcher..."
uv run jupyter-book clean .
uv run jupyter-book build --builder dirhtml .
uv run watchmedo shell-command \
    --patterns="*.md" \
    --recursive \
    --drop \
    --command='echo "Rebuilding..." && uv run jupyter-book clean . && uv run jupyter-book build --builder dirhtml .' \
    .
