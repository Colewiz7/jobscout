.PHONY: test test-pg audit-phase2 audit-phase3 audit-phase4 audit-queue-prod audit-phase5 audit-phase6 audit-overview

PYTHON ?= .venv/bin/python

test:
	$(PYTHON) -m pytest -q
	node --test tests/test_highlights.mjs

test-pg:
	PYTHON="$(PYTHON)" ./scripts/test-pg.sh

audit-phase2:
	node scripts/audit-phase2.mjs

audit-phase3:
	JOBSCOUT_AUDIT_QUICK_FILL=true node scripts/audit-phase2.mjs

audit-phase4:
	JOBSCOUT_AUDIT_QUICK_FILL=true JOBSCOUT_AUDIT_APPLY_SESSION=true node scripts/audit-phase2.mjs

audit-queue-prod:
	JOBSCOUT_AUDIT_APPLY_SESSION=true JOBSCOUT_AUDIT_SESSION_NO_PROFILE=true node scripts/audit-phase2.mjs

audit-phase5:
	JOBSCOUT_AUDIT_TRACKER=true node scripts/audit-phase2.mjs

audit-phase6:
	JOBSCOUT_AUDIT_ELIGIBILITY=true node scripts/audit-phase2.mjs

audit-overview:
	JOBSCOUT_AUDIT_OVERVIEW=true node scripts/audit-phase2.mjs
