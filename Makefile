.PHONY: test test-pg audit-phase2

PYTHON ?= .venv/bin/python

test:
	$(PYTHON) -m pytest -q

test-pg:
	PYTHON="$(PYTHON)" ./scripts/test-pg.sh

audit-phase2:
	node scripts/audit-phase2.mjs
