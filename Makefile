# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

DATABASE ?= postgresql
PUBLIC_SUBMODULES = r2rml_test_cases rml_io_registry

.PHONY: validate-conformance-options submodules rmlmapper-assets test-conformance

validate-conformance-options:
	@case "$(DATABASE)" in \
		postgresql|mysql) ;; \
		*) echo "DATABASE must be postgresql or mysql" >&2; exit 2 ;; \
	esac

submodules:
	git submodule update --init --recursive $(PUBLIC_SUBMODULES)

rmlmapper-assets:
	docker build --target rmlmapper-assets -t kgi-rmlmapper-assets .
	@set -e; \
	mkdir -p build; \
	container=$$(docker create kgi-rmlmapper-assets); \
	trap 'docker rm "$$container" >/dev/null' EXIT; \
	docker cp "$$container:/rmlmapper/rmlmapper.jar" build/rmlmapper-8.1.0-binary-null.jar

test-conformance: validate-conformance-options submodules rmlmapper-assets
	uv run pytest tests/test_conformance.py -v --database=$(DATABASE)
