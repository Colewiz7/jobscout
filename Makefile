.PHONY: test test-pg audit-phase2 audit-phase3

PYTHON ?= .venv/bin/python

test:
	$(PYTHON) -m pytest -q

test-pg:
	PYTHON="$(PYTHON)" ./scripts/test-pg.sh

audit-phase2:
	node scripts/audit-phase2.mjs

audit-phase3:
	JOBSCOUT_AUDIT_QUICK_FILL=true node scripts/audit-phase2.mjs
