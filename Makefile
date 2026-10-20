# AI Control Graph
DB ?= postgresql:///control_graph
PY := uv run --quiet --with 'psycopg[binary]' --with pypdf python -I

.PHONY: help doctor migrate seed graph verify test clean

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

doctor:        ## check credentials and report what works vs what is degraded
	@$(PY) scripts/doctor.py

migrate:       ## apply schema to $$DATABASE_URL
	@psql "$(DB)" -v ON_ERROR_STOP=1 -q -f db/migrations/001_schema.sql
	@psql "$(DB)" -v ON_ERROR_STOP=1 -q -f db/migrations/002_graph_functions.sql
	@echo "migrations applied"

crosswalk:     ## re-parse NIST's crosswalk PDF into seed JSON
	@$(PY) graph/parse_crosswalk.py

graph:         ## build the control graph from seed files (idempotent)
	@DATABASE_URL="$(DB)" $(PY) graph/load.py

seed: migrate graph  ## migrate + build graph

verify:        ## assert graph integrity and propagation behaviour
	@DATABASE_URL="$(DB)" uv run --quiet --with 'psycopg[binary]' --with pytest \
		python -m pytest tests -q

clean:
	@dropdb --if-exists control_graph && createdb control_graph
