# 001. Hold the configs to the same behaviour, not the same bytes

Status: accepted, 2026-09-25

## Context

The filters live in two places. This repo ships `config/filters.yaml` as a
working default. The cluster runs a ConfigMap in the gitops repo carrying the
same filters plus 264 boards and the employers being targeted.

They drifted. The title pattern gained an exclusion for go-to-market roles and
two AWS-specific shapes in the deployed copy only. The test suite kept passing
because it tests the repo default, so for some weeks the tests were green
against rules that were not the rules in production. It surfaced by accident,
when a test written against production behaviour failed locally.

## Decision

Pin the behaviour, not the file. `tests/contract_cases.yaml` holds titles,
locations and levels with their expected verdicts. This repo runs it against
its own default. The gitops repo runs the same file, fetched from here,
against the ConfigMap it deploys. Either side drifting fails a build.

## Why not a single source of truth

Because the two files must differ. The deployed one names the employers being
watched, and that list is deliberately not public. Generating one from the
other means either publishing the target list or moving the filters into the
private repo, where they would lose the test suite that gives them meaning.

## Consequences

The configs can be spelled differently as long as they decide the same way,
which is the property that actually matters. The cost is that a new rule needs
a case added to the contract, or it is unguarded. The contract is 44 cases and
runs in under a second on both sides.
