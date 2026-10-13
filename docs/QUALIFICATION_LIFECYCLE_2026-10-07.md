# Qualification lifecycle — 2026-10-07

This addition preserves ThinkerQL → resolver → adapter → execution evidence. It adds no routing optimizer, financial semantics or application framework.

## Applicability

An ordinary call needs the latest PASS for the exact profile/contract/provider/model/adapter/configuration/code/runtime fingerprint, the complete expected matrix of verified LIVE_PROBE execution artifacts, an unexpired finite lease, and no unacknowledged drift event. A historical PASS without lease fields is not applicable. A failed/review requalification does not fall back to an older PASS.

Profiles configure `validity_seconds` (default 86400; minimum60, maximum604800). All targets expire, including named snapshots: model naming cannot guarantee immutable service behavior. Records contain `started_at`, `expires_at`, `validity_seconds`, and `invalidation_head`. Expiry is measured from suite start, not completion or subsequent runtime activity. Clock times before suite start fail closed.

## Drift and quarantine

`qualification_state.record_drift(root, target, state, execution_ref, reason)` appends a hash-addressed event. State is SUSPECT or QUARANTINED; every event verifies its exact execution evidence and target identity. Events form an append-only chain. A weaker subsequent event cannot clear quarantine.

Runtime failures append SUSPECT for infrastructure uncertainty or QUARANTINED for completed contract violations/known identity-output violations. Runtime success creates execution evidence only: no PASS, no new lease, no drift reset. Drift identity binds provider/model/adapter/configuration independently of application contract version.

Only a fresh explicitly authorized qualification suite can acknowledge the current drift head. Any later event, including an event during that suite, makes its result inapplicable. No automatic requalification or model selection occurs. The resolver selects applicable immutable evidence; previous records and events remain unchanged.

Files: `qualification.py` (lease fields, applicability gate, runtime drift capture); `qualification_state.py` (small functional event/expiry operations); `tests/test_qualification_lifecycle.py` (two complete lifecycle scenarios). Existing CAS store and external-validator protocol remain unchanged.

## Verification and first live result

Thinker full offline regression: 226 passed, 2 skipped. Qualification scenarios: 12 passed.

The explicitly authorized experiment is documented in `/Users/anjan/foundation/gandiva-eval/docs/QUALIFICATION_LIVE_RESULTS_2026-10-07.md`. Its corrected configuration made 14 HTTP requests over seven frozen cases. OpenAI returned seven structured outputs, all rejected by the external Gandiva validator, and is QUARANTINED. Anthropic returned seven authentication errors and is SUSPECT; its semantic capability was not evaluated. No target passed. An initial registry configuration failed resolution before making any HTTP call; its artifacts remain separate and immutable.

The experiment did not configure production routing. Thinker still owns only generic execution/contract enforcement and evidence recording; Gandiva-eval owns PIT and financial-semantic judgments. The repository remains a trusted-host implementation: hashes detect altered stored bytes but are not a signature, external transparency service or protection from a hostile host administrator deleting whole histories.
