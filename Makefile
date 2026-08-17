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
CONFORMANCE_SOUFFLE_IMAGE = alloka/souffle:v1.0.0@sha256:0e9288ca6f7a63faf93f4358f210de0ffcab6e3e2405d88c365391da6d54fe89
MAVEN_IMAGE = maven:3.9.11-eclipse-temurin-17
PUBLIC_SUBMODULES = KROWN KROWN_Extended R2RML2Datalog-Translator gtfs-bench r2rml_test_cases rml_io_registry
REVERSE_SUBMODULE = ReverseR2RML
R2RML_TRANSLATOR_SOURCE = R2RML2Datalog-Translator
R2RML_TRANSLATOR_COMMIT = $(word 2,$(shell git ls-files --stage $(R2RML_TRANSLATOR_SOURCE)))
R2RML_TRANSLATOR_BUILD = build/r2rml2datalog-translator-$(R2RML_TRANSLATOR_COMMIT)
R2RML_TRANSLATOR_JAR = $(R2RML_TRANSLATOR_BUILD)/translator/target/rulegen.jar
R2RML_FUNCTOR_LIBRARY = $(R2RML_TRANSLATOR_BUILD)/lib/libfunctors.so
KROWN_I = $(if $(strip $(I)),$(I),5)
GTFS_I = $(if $(strip $(I)),$(I),10)
KROWN_SCENARIO_ARG = $(if $(SCENARIO),--scenario=$(SCENARIO))
KROWN_RESUME_ARG = $(if $(RESUME),--resume=$(RESUME))
KROWN_RUN = uv run python -m benchmarks.run_krown_benchmark --mode $(MODE) --iterations $(KROWN_I) --interval $(INTERVAL) --suites $(SUITES) --forward-engine $(FORWARD_ENGINE) --inversion-engine $(INVERSION_ENGINE) --souffle-provenance $(SOUFFLE_PROVENANCE) $(KROWN_SCENARIO_ARG) $(KROWN_RESUME_ARG)

.PHONY: validate-krown-options validate-conformance-options submodules reverse-submodule translator-assets krown-images benchmark-krown benchmark-gtfs benchmark-all test-conformance

validate-krown-options:
	@case "$(SOUFFLE_PROVENANCE)" in \
		true|false) ;; \
		*) echo "SOUFFLE_PROVENANCE must be true or false" >&2; exit 2 ;; \
	esac

validate-conformance-options:
	@case "$(FORWARD_ENGINE)/$(INVERSION_ENGINE)" in \
		rmlmapper/kgi|souffle/souffle) ;; \
		*) echo "FORWARD_ENGINE/INVERSION_ENGINE must be rmlmapper/kgi or souffle/souffle" >&2; exit 2 ;; \
	esac
	@case "$(DATABASE)" in \
		postgresql|mysql) ;; \
		*) echo "DATABASE must be postgresql or mysql" >&2; exit 2 ;; \
	esac

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
			-std=c++17 -shared -fPIC /work/functors.cpp -o /work/lib/libfunctors.so; \
	fi; \
	unzip -p "$(R2RML_TRANSLATOR_JAR)" META-INF/MANIFEST.MF | grep -q '^Main-Class: translator.r2rml.datalog.Main'; \
	jar tf "$(R2RML_TRANSLATOR_JAR)" | grep -q '^translator/r2rml/datalog/Main.class$$'; \
	test -s "$(R2RML_FUNCTOR_LIBRARY)"

krown-images:
	@set -e; \
	if [ "$(FORWARD_ENGINE)" = "rmlmapper" ]; then \
		docker build --target krown-rmlmapper -t $(KROWN_RMLMAPPER_IMAGE) .; \
	fi; \
	if [ "$(FORWARD_ENGINE)" = "souffle" ] || [ "$(INVERSION_ENGINE)" = "souffle" ]; then \
		$(MAKE) reverse-submodule; \
		docker build --target krown-souffle -t $(KROWN_SOUFFLE_IMAGE) .; \
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

test-conformance: validate-conformance-options
	@$(MAKE) submodules
	@if [ "$(FORWARD_ENGINE)/$(INVERSION_ENGINE)" = "souffle/souffle" ]; then \
		$(MAKE) reverse-submodule; \
		$(MAKE) translator-assets; \
		uv run pytest tests/souffle_conformance.py -v --database=$(DATABASE) \
			--souffle-jar="$(abspath $(R2RML_TRANSLATOR_JAR))" \
			--souffle-library="$(abspath $(R2RML_FUNCTOR_LIBRARY))"; \
	else \
		uv run pytest tests/test_conformance.py -v --database=$(DATABASE); \
	fi
