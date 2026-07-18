# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

COMPOSE_KROWN = docker compose -f docker-compose.benchmark.yml
COMPOSE_GTFS = docker compose -f docker-compose.benchmark.yml --profile gtfs
I ?=
S ?= 1,5,10
KROWN_SUITES ?= all
KROWN_SCENARIO ?=
KROWN_MODE ?= roundtrip
KROWN_INTERVAL ?= 0.1
KROWN_REVISION = 2dd04d84b5e95a0b305f8854db5d51b42c160101
KROWN_RMLMAPPER_IMAGE = kgconstruct/rmlmapper:v8.1.0
KROWN_I = $(if $(strip $(I)),$(I),5)
GTFS_I = $(if $(strip $(I)),$(I),10)
KROWN_SCENARIO_ARG = $(if $(KROWN_SCENARIO),--scenario=$(KROWN_SCENARIO))

.PHONY: submodules benchmark-krown benchmark-gtfs benchmark-all test-conformance

submodules:
	@set -e; \
	git submodule sync --recursive; \
	git submodule update --init --recursive -- gtfs-bench r2rml_test_cases rml_io_registry; \
	if [ "$$(git -C KROWN rev-parse HEAD 2>/dev/null || true)" != "$(KROWN_REVISION)" ]; then \
		git submodule update --init --recursive -- KROWN; \
		git -C KROWN cat-file -e "$(KROWN_REVISION)^{commit}" 2>/dev/null || \
			git -C KROWN fetch origin "$(KROWN_REVISION)"; \
		git -C KROWN checkout --quiet --detach "$(KROWN_REVISION)"; \
	fi

benchmark-krown: submodules
	@set -e; \
	trap '$(COMPOSE_KROWN) down --remove-orphans' EXIT; \
	docker build --target krown-rmlmapper -t $(KROWN_RMLMAPPER_IMAGE) .; \
	if [ "$(KROWN_MODE)" != "forward" ]; then \
		$(COMPOSE_KROWN) build benchmark; \
	fi; \
	uv run python -m benchmarks.run_krown_benchmark --mode $(KROWN_MODE) --iterations $(KROWN_I) --interval $(KROWN_INTERVAL) --suites $(KROWN_SUITES) $(KROWN_SCENARIO_ARG)

benchmark-gtfs: submodules
	@set -e; \
	trap '$(COMPOSE_GTFS) down --remove-orphans' EXIT; \
	$(COMPOSE_GTFS) build benchmark; \
	$(COMPOSE_GTFS) up -d gtfs_mysql; \
	$(COMPOSE_GTFS) run --rm benchmark gtfs-benchmark --iterations $(GTFS_I) --scales $(S)

benchmark-all: submodules
	@set -e; \
	trap '$(COMPOSE_GTFS) down --remove-orphans' EXIT; \
	docker build --target krown-rmlmapper -t $(KROWN_RMLMAPPER_IMAGE) .; \
	$(COMPOSE_GTFS) build benchmark; \
	uv run python -m benchmarks.run_krown_benchmark --mode $(KROWN_MODE) --iterations $(KROWN_I) --interval $(KROWN_INTERVAL) --suites $(KROWN_SUITES) $(KROWN_SCENARIO_ARG); \
	$(COMPOSE_GTFS) up -d gtfs_mysql; \
	$(COMPOSE_GTFS) run --rm benchmark gtfs-benchmark --iterations $(GTFS_I) --scales $(S)

test-conformance:
	uv run pytest tests/test_conformance.py -v
