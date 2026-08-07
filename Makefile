# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

COMPOSE_KROWN = docker compose -f docker-compose.benchmark.yml
COMPOSE_GTFS = docker compose -f docker-compose.benchmark.yml --profile gtfs
I ?=
S ?= 1,5,10
SUITES ?= all
SCENARIO ?=
RESUME ?=
MODE ?= roundtrip
FORWARD_ENGINE ?= rmlmapper
INVERSION_ENGINE ?= kgi
INTERVAL ?= 0.1
SOUFFLE_PROVENANCE ?= false
DATABASE ?= postgresql
KROWN_RMLMAPPER_IMAGE = kgconstruct/rmlmapper:v8.1.0
KROWN_SOUFFLE_IMAGE = alloka/souffle:v1.0.0
KROWN_SOUFFLE_CONTEXT = KROWN_Extended/execution-framework/dockers/Souffle
KROWN_I = $(if $(strip $(I)),$(I),5)
GTFS_I = $(if $(strip $(I)),$(I),10)
KROWN_SCENARIO_ARG = $(if $(SCENARIO),--scenario=$(SCENARIO))
KROWN_RESUME_ARG = $(if $(RESUME),--resume=$(RESUME))
KROWN_RUN = uv run python -m benchmarks.run_krown_benchmark --mode $(MODE) --iterations $(KROWN_I) --interval $(INTERVAL) --suites $(SUITES) --forward-engine $(FORWARD_ENGINE) --inversion-engine $(INVERSION_ENGINE) --souffle-provenance $(SOUFFLE_PROVENANCE) $(KROWN_SCENARIO_ARG) $(KROWN_RESUME_ARG)

.PHONY: validate-krown-options submodules krown-images benchmark-krown benchmark-gtfs benchmark-all test-conformance

validate-krown-options:
	@case "$(SOUFFLE_PROVENANCE)" in \
		true|false) ;; \
		*) echo "SOUFFLE_PROVENANCE must be true or false" >&2; exit 2 ;; \
	esac

submodules:
	git submodule update --init --recursive

krown-images:
	@set -e; \
	if [ "$(FORWARD_ENGINE)" = "rmlmapper" ]; then \
		docker build --target krown-rmlmapper -t $(KROWN_RMLMAPPER_IMAGE) .; \
	fi; \
	if [ "$(FORWARD_ENGINE)" = "souffle" ] || [ "$(INVERSION_ENGINE)" = "souffle" ]; then \
		docker build -t $(KROWN_SOUFFLE_IMAGE) $(KROWN_SOUFFLE_CONTEXT); \
	fi

benchmark-krown: validate-krown-options submodules krown-images
	@set -e; \
	trap '$(COMPOSE_KROWN) down --remove-orphans' EXIT; \
	if [ "$(MODE)" != "forward" ]; then \
		$(COMPOSE_KROWN) build benchmark; \
	fi; \
	$(KROWN_RUN)

benchmark-gtfs: submodules
	@set -e; \
	trap '$(COMPOSE_GTFS) down --remove-orphans' EXIT; \
	$(COMPOSE_GTFS) build benchmark; \
	$(COMPOSE_GTFS) up -d gtfs_mysql; \
	$(COMPOSE_GTFS) run --rm benchmark gtfs-benchmark --iterations $(GTFS_I) --scales $(S)

benchmark-all: validate-krown-options submodules krown-images
	@set -e; \
	trap '$(COMPOSE_GTFS) down --remove-orphans' EXIT; \
	$(COMPOSE_GTFS) build benchmark; \
	$(KROWN_RUN); \
	$(COMPOSE_GTFS) up -d gtfs_mysql; \
	$(COMPOSE_GTFS) run --rm benchmark gtfs-benchmark --iterations $(GTFS_I) --scales $(S)

test-conformance:
	uv run pytest tests/test_conformance.py -v --database=$(DATABASE)
