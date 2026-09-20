# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

COMPOSE_KROWN = docker compose -f docker-compose.benchmark.yml
COMPOSE_GTFS = docker compose -f docker-compose.benchmark.yml --profile gtfs
SCENARIO ?=
RESUME ?=
ifneq ($(filter benchmark-krown benchmark-all,$(MAKECMDGOALS)),)
$(foreach option,I SUITES INTERVAL,$(if $(strip $($(option))),,$(error Missing required benchmark parameter: $(option))))
endif
FORWARD_ENGINE ?= rmlmapper
INVERSION_ENGINE ?= kgi
ifneq ($(filter benchmark-gtfs benchmark-all,$(MAKECMDGOALS)),)
$(foreach option,I S,$(if $(strip $($(option))),,$(error Missing required benchmark parameter: $(option))))
endif
SHEET ?= $(KROWN_SHEET_ID)
CREDENTIALS ?=
ifneq ($(filter export-krown-sheets,$(MAKECMDGOALS)),)
$(foreach option,STATS SHEET,$(if $(strip $($(option))),,$(error Missing required export parameter: $(option))))
endif
KROWN_CREDENTIALS_ARG = $(if $(CREDENTIALS),--credentials=$(CREDENTIALS))
SOUFFLE_MODES ?= rdf,provenance,hybrid
DATABASE ?= postgresql
KROWN_SOUFFLE_IMAGE = alloka/souffle:v1.0.0
CONFORMANCE_SOUFFLE_IMAGE = alloka/souffle:v1.0.0@sha256:0e9288ca6f7a63faf93f4358f210de0ffcab6e3e2405d88c365391da6d54fe89
MAVEN_IMAGE = maven:3.9.11-eclipse-temurin-17
PUBLIC_SUBMODULES = KROWN R2RML2Datalog-Translator gtfs-bench r2rml_test_cases rml_io_registry
REVERSE_SUBMODULE = ReverseR2RML
REVERSE_SCRIPT ?= $(abspath $(REVERSE_SUBMODULE)/reverseR2RML.py)
R2RML_TRANSLATOR_SOURCE = R2RML2Datalog-Translator
R2RML_TRANSLATOR_COMMIT = $(word 2,$(shell git ls-files --stage $(R2RML_TRANSLATOR_SOURCE)))
R2RML_TRANSLATOR_BUILD = build/r2rml2datalog-translator-$(R2RML_TRANSLATOR_COMMIT)
R2RML_TRANSLATOR_JAR = $(R2RML_TRANSLATOR_BUILD)/translator/target/rulegen.jar
R2RML_FUNCTOR_LIBRARY = $(R2RML_TRANSLATOR_BUILD)/lib/libfunctors.so
KROWN_SCENARIO_ARG = $(if $(SCENARIO),--scenario=$(SCENARIO))
KROWN_RESUME_ARG = $(if $(RESUME),--resume=$(RESUME))
KROWN_RUN = uv run python -m benchmarks.run_krown_benchmark --iterations $(I) --interval $(INTERVAL) --suites $(SUITES) $(KROWN_SCENARIO_ARG) $(KROWN_RESUME_ARG)

.PHONY: validate-conformance-options validate-sheets-options submodules reverse-submodule translator-assets krown-images krown-network benchmark-krown benchmark-gtfs benchmark-all test-conformance export-krown-sheets

validate-conformance-options:
	@case "$(FORWARD_ENGINE)/$(INVERSION_ENGINE)" in \
		rmlmapper/kgi|souffle/souffle) ;; \
		*) echo "FORWARD_ENGINE/INVERSION_ENGINE must be rmlmapper/kgi or souffle/souffle" >&2; exit 2 ;; \
	esac
	@case "$(DATABASE)" in \
		postgresql|mysql) ;; \
		*) echo "DATABASE must be postgresql or mysql" >&2; exit 2 ;; \
	esac

validate-sheets-options:
	@test -r "$(STATS)" || { echo "STATS is not readable: $(STATS)" >&2; exit 2; }

export-krown-sheets: validate-sheets-options
	uv run python -m benchmarks.krown_sheets $(STATS) --spreadsheet-id $(SHEET) $(KROWN_CREDENTIALS_ARG)

submodules:
	git submodule update --init --recursive $(PUBLIC_SUBMODULES)

reverse-submodule:
	git submodule update --init $(REVERSE_SUBMODULE)

translator-assets:
	@set -e; \
	test "$$(git -C $(R2RML_TRANSLATOR_SOURCE) rev-parse HEAD)" = "$(R2RML_TRANSLATOR_COMMIT)"; \
	if [ ! -f "$(R2RML_TRANSLATOR_JAR)" ] || [ ! -f "$(R2RML_FUNCTOR_LIBRARY)" ]; then \
		if [ -d "$(R2RML_TRANSLATOR_BUILD)" ]; then rm -r "$(R2RML_TRANSLATOR_BUILD)"; fi; \
		mkdir -p "$(R2RML_TRANSLATOR_BUILD)"; \
		git -C $(R2RML_TRANSLATOR_SOURCE) archive $(R2RML_TRANSLATOR_COMMIT) | tar -x -C "$(R2RML_TRANSLATOR_BUILD)"; \
		mkdir -p build/maven-repository; \
		docker run --rm --user "$$(id -u):$$(id -g)" \
			-e MAVEN_CONFIG=/build/maven-repository \
			-v "$(abspath build):/build" \
			-w "/build/r2rml2datalog-translator-$(R2RML_TRANSLATOR_COMMIT)/translator" \
			$(MAVEN_IMAGE) mvn --quiet \
			-Dmaven.repo.local=/build/maven-repository \
			-Dproject.build.outputTimestamp=1980-01-01T00:00:02Z package; \
		mkdir -p "$(R2RML_TRANSLATOR_BUILD)/lib"; \
		docker run --rm --user "$$(id -u):$$(id -g)" \
			-v "$(abspath $(R2RML_TRANSLATOR_BUILD)):/work" \
			--entrypoint g++ $(CONFORMANCE_SOUFFLE_IMAGE) \
			-std=c++17 -I/souffle/include -DRAM_DOMAIN_SIZE=32 \
			-shared -fPIC /work/functors.cpp -o /work/lib/libfunctors.so; \
	fi; \
	unzip -p "$(R2RML_TRANSLATOR_JAR)" META-INF/MANIFEST.MF | grep -q '^Main-Class: translator.r2rml.datalog.Main'; \
	jar tf "$(R2RML_TRANSLATOR_JAR)" | grep -q '^translator/r2rml/datalog/Main.class$$'; \
	test -s "$(R2RML_FUNCTOR_LIBRARY)"

krown-images: reverse-submodule
	docker build --target krown-souffle -t $(KROWN_SOUFFLE_IMAGE) .

krown-network:
	@docker network inspect bench_executor >/dev/null 2>&1 || docker network create bench_executor >/dev/null

benchmark-krown: submodules krown-images krown-network
	@set -e; \
	trap '$(COMPOSE_KROWN) down --remove-orphans' EXIT; \
	$(COMPOSE_KROWN) build benchmark; \
	$(COMPOSE_KROWN) up -d benchmark_postgresql; \
	$(KROWN_RUN)

benchmark-gtfs: submodules krown-network
	@set -e; \
	trap '$(COMPOSE_GTFS) down --remove-orphans' EXIT; \
	$(COMPOSE_GTFS) build benchmark; \
	$(COMPOSE_GTFS) up -d gtfs_mysql; \
	$(COMPOSE_GTFS) run --rm benchmark gtfs-benchmark --iterations $(I) --scales $(S)

benchmark-all: submodules krown-images krown-network
	@set -e; \
	trap '$(COMPOSE_GTFS) down --remove-orphans' EXIT; \
	$(COMPOSE_GTFS) build benchmark; \
	$(COMPOSE_KROWN) up -d benchmark_postgresql; \
	$(KROWN_RUN); \
	$(COMPOSE_GTFS) up -d gtfs_mysql; \
	$(COMPOSE_GTFS) run --rm benchmark gtfs-benchmark --iterations $(I) --scales $(S)

test-conformance: validate-conformance-options
	@$(MAKE) submodules
	@if [ "$(FORWARD_ENGINE)/$(INVERSION_ENGINE)" = "souffle/souffle" ]; then \
		$(MAKE) reverse-submodule; \
		$(MAKE) translator-assets; \
		uv run pytest tests/souffle_conformance.py -v --database=$(DATABASE) \
			--souffle-jar="$(abspath $(R2RML_TRANSLATOR_JAR))" \
			--souffle-library="$(abspath $(R2RML_FUNCTOR_LIBRARY))" \
			--reverse-script="$(REVERSE_SCRIPT)" \
			--souffle-modes="$(SOUFFLE_MODES)"; \
	else \
		uv run pytest tests/test_conformance.py -v --database=$(DATABASE); \
	fi
