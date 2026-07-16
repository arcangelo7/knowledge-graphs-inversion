# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

COMPOSE_KROWN = docker compose -f docker-compose.benchmark.yml
COMPOSE_GTFS = docker compose -f docker-compose.benchmark.yml --profile gtfs
I ?= 10
S ?= 1,5,10
KROWN_SUITES ?= all
KROWN_SCENARIO ?=
KROWN_SCENARIO_ARG = $(if $(KROWN_SCENARIO),--scenario=$(KROWN_SCENARIO))

.PHONY: benchmark-krown benchmark-gtfs benchmark-all test-conformance

benchmark-krown:
	@set -e; \
	git submodule update --init --recursive; \
	trap '$(COMPOSE_KROWN) down --remove-orphans' EXIT; \
	$(COMPOSE_KROWN) build benchmark; \
	$(COMPOSE_KROWN) run --rm benchmark krown-benchmark --iterations $(I) --suites $(KROWN_SUITES) $(KROWN_SCENARIO_ARG)

benchmark-gtfs:
	@set -e; \
	git submodule update --init --recursive; \
	trap '$(COMPOSE_GTFS) down --remove-orphans' EXIT; \
	$(COMPOSE_GTFS) build benchmark; \
	$(COMPOSE_GTFS) up -d gtfs_mysql; \
	$(COMPOSE_GTFS) run --rm benchmark gtfs-benchmark --iterations $(I) --scales $(S)

benchmark-all:
	@set -e; \
	git submodule update --init --recursive; \
	trap '$(COMPOSE_GTFS) down --remove-orphans' EXIT; \
	$(COMPOSE_GTFS) build benchmark; \
	$(COMPOSE_KROWN) run --rm benchmark krown-benchmark --iterations $(I) --suites $(KROWN_SUITES) $(KROWN_SCENARIO_ARG); \
	$(COMPOSE_GTFS) up -d gtfs_mysql; \
	$(COMPOSE_GTFS) run --rm benchmark gtfs-benchmark --iterations $(I) --scales $(S)

test-conformance:
	uv run pytest tests/test_conformance.py -v
