#!/bin/bash

# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

set -euo pipefail

cd "$(dirname "$0")/docs"

uv run jupyter-book clean --html --site --logs --temp -y
uv run jupyter-book build --html --strict
