# Model-Turn observations and implementation tasks — 2026-10-07

Status: **PLANNED — documentation only**

This review examines the existing Model-Turn V1 implementation for reusable, auditable calls to thinking models and native structured-output APIs. It was prompted by Gandiva's GE2-A3 evaluation. Findings are based on local code inspection and offline request checks, not new live model executions. No runtime fixes are claimed by this document.

## Purpose and ownership

Thinker is the appropriate home for provider routing, credential resolution, thinking controls, tool exposure, structural generation, execution outcomes, usage and transport provenance. These capabilities are useful beyond Gandiva.

Gandiva and gandiva-eval retain ownership of evidence, ontology, semantic prompts, ReasoningSpec, semantic validation, admission, investigation state and evaluation. Thinker must not interpret SUPPORTS/CHALLENGES, select financial conclusions, repair semantic relationships or acquire financial evidence on the caller's behalf.

The production Gandiva → Spartacus path currently invokes Claude CLI; it does not yet use Thinker's direct provider APIs. Migrating that path is separate work. Preserve Spartacus's evidence/research-dump ownership and Gandiva's interpretation boundary.

## What already exists

- [Model-Turn V1](MODEL_TURN_V1.md) supplies a provider-neutral single-turn request/response contract.
- Native adapters exist for OpenAI Responses, Anthropic Messages and Gemini GenerateContent.
- Requests can supply a schema and an empty tool list; Thinker does not execute tools.
- Responses expose resolved provider/model/route, usage, finish reason and normalized errors.
- OpenAI reasoning-token counts and Gemini thought-token counts are partially normalized.
- Gemini retains thought signatures as opaque tool-continuation state.
- Current telemetry intentionally excludes prompts and outputs. Preserve that privacy default when adding opt-in audit artifacts.

## Observed gaps

### G1 — Thinking controls cannot be expressed explicitly

[GenerationParameters](../thinker-core/thinker/model_turn/models.py) contains output tokens, temperature, top_p, seed and stop, but no reasoning effort, thinking mode or thinking budget. Provider payload builders do not expose those settings through the shared contract.

Consequences: callers cannot freeze an explicit thinking configuration; provider defaults may differ. Unsupported parameter combinations also need capability-aware rejection. Do not pretend that an effort label and a token budget are interchangeable across providers.

### G2 — Gemini thought text can contaminate the final answer

[Gemini `_parse_response`](../thinker-core/thinker/model_turn/gemini.py) appends every textual part to `assistant_content` without testing `thought: true`. A returned thought summary followed by JSON could therefore become a non-JSON combined answer. This is a code-path finding, not an observed live GE2-A3 failure.

The evaluation-side Gemini decoder now excludes thought parts, but that does not fix Thinker's parser. OpenAI only extracts `output_text`, and Anthropic only extracts `text` blocks; these paths already avoid their separate reasoning/thinking blocks. Their behavior still needs explicit conformance tests.

Distinguish four things: final answer, optional provider-exposed thought summary, opaque continuation/signature data, and reasoning-token usage. None is a substitute for another. Do not request hidden chain-of-thought or promote thought summaries into evidence.

### G3 — Native schema enforcement is not guaranteed

[OpenAI `turn`](../thinker-core/thinker/model_turn/openai.py) performs another HTTP POST after `400 invalid_json_schema`, replacing native strict output with ordinary JSON mode plus schema instructions. Its payload builder also uses this weaker mode when native capability is absent.

[Runtime `_resolve`](../thinker-core/thinker/model_turn/runtime.py) requires JSON_MODE for schema requests, not necessarily JSON_SCHEMA. This matches the existing documented best-effort V1 behavior; it is insufficient for an experiment that requires native enforcement and exactly one attempt.

The fallback changes the generation contract and prompt and can turn one scheduled slot into two provider requests. A strict caller must be able to prohibit it explicitly. Preserve or version legacy behavior deliberately rather than silently changing old experiments.

### G4 — Transport termination and structural validity are conflated

The runtime validates structured output before representing a distinct refusal/incomplete state. OpenAI parsing does not explicitly extract refusal parts; Gemini expects candidate content before accounting for prompt blocking. Anthropic retains stop reason but the runtime still attempts schema validation.

A refused or truncated answer can become `StructuredOutputViolation` or malformed-response error. Distinguish execution/termination, structural parsing and application semantic admission. A provider refusal is not a Gandiva semantic rejection.

### G5 — Post-generation failures lose known usage

[Runtime `_failure_response`](../thinker-core/thinker/model_turn/runtime.py) constructs empty Usage and Cost even if a response was received and subsequent tool/schema validation failed. Known billed work can therefore disappear from the normalized failure record.

Usage defaults and total normalization also blur unavailable information with zero. Preserve raw provider-reported accounting and distinguish unknown from measured zero. Reasoning tokens may already be included in output tokens; avoid double counting. Preserve cached-input details and identify estimated cost and pricebook version. Do not invent a reasoning-token breakdown when a provider does not supply one.

### G6 — Raw provider artifacts are unavailable at the public boundary

[ProviderTurnResult](../thinker-core/thinker/model_turn/provider.py) and [ModelTurnResponse](../thinker-core/thinker/model_turn/models.py) do not expose a complete raw provider envelope. The runtime observer emits metadata only. GE2-A3 requires exact outbound payloads and received responses to audit transformations and failures.

Add an explicit opt-in artifact sink or caller-owned capture mechanism, separate from ordinary telemetry. Capture payloads without credentials, preserve error bodies and partial output where available, and record any redaction. Do not log authorization headers, keys, environment contents or private model reasoning requests. Provider-exposed summaries and opaque state require explicit retention/access policies.

### G7 — Reproducibility does not yet bind all execution settings

Route/model identity and Thinker revision already exist, but callers also need requested/resolved thinking settings, structural enforcement mode, attempt count, schema/prompt hashes, tool policy, timestamps and termination reason. An alias alone is insufficient for a frozen comparison.

### G8 — Cancellation is cooperative, not confirmed remote cancellation

The runtime signals a daemon worker and ignores late results. The synchronous provider HTTP request may continue until its network timeout; server-side billing may continue. This limitation is already documented and should remain explicit. Track late completion/available usage safely rather than falsely claiming that cancellation stopped provider computation. Deadline accounting must encompass any allowed attempts, not grant each retry a fresh overall budget.

## Required tasks

All tasks below are unchecked and **PLANNED**. Exact field names and protocol version remain design decisions.

### P0 — Define a compatible execution contract

- [ ] T1: Specify a versioned extension or next Model-Turn version; document existing-client behavior and migration.
- [ ] T2: Add explicit structured-output policy: native schema required versus deliberately requested best effort. Report actual enforcement mode.
- [ ] T3: Add explicit attempt policy; strict evaluation allows one provider attempt, no fallback, no repair, no route substitution.
- [ ] T4: Add capability-validated thinking settings. Preserve provider-specific distinctions with typed settings, not unrestricted request overrides.
- [ ] T5: Separate execution/termination states from structural validation. Keep application semantic admission outside Thinker.
- [ ] T6: Represent usage availability and partial accounting; preserve known metadata on failed validation.

### P0 — Correct provider and runtime behavior

- [ ] T7: Filter Gemini thought text from final-answer content. Keep signatures opaque; preserve required continuation semantics without exposing them as reasoning evidence.
- [ ] T8: In strict mode, fail on unsupported native schema or provider schema rejection without a second request or modified prompt.
- [ ] T9: Map provider refusal, blocking, incomplete and error states before attempting canonical output parsing. Preserve raw finish reasons.
- [ ] T10: Map validated thinking controls for OpenAI, Anthropic and Gemini using current official API documentation and model-specific capabilities. Record effective settings.
- [ ] T11: Preserve usage/cost provenance after parsing/tool/schema errors. Unknown usage remains unknown; no zero-cost inference from missing counters.
- [ ] T12: Enforce empty-tool policy across all native adapters and reject unexpected tool output. Do not execute tools inside Thinker.

### P1 — Add reusable auditability

- [ ] T13: Implement opt-in immutable transport capture: sanitized outbound body, inbound body, request IDs, HTTP status, attempt ledger, timing and resolved identity. Preserve available failure artifacts without fabricating missing stages.
- [ ] T14: Retain privacy-preserving metadata-only observability as the default. Define retention/redaction for provider-exposed thought summaries and opaque continuation state.
- [ ] T15: Bind prompt/schema/settings hashes to the request; record requested and resolved model identity and Thinker revision. Freeze route/configuration before benchmark collection.
- [ ] T16: Record cancellation/deadline behavior accurately, including whether remote cancellation is unconfirmed and usage incomplete.

### P1 — Deterministic conformance tests

- [ ] T17: Synthetic thinking+JSON response produces only the final JSON as `assistant_content` for every provider. Include redacted/opaque blocks and malformed variants.
- [ ] T18: Native-schema rejection in strict mode yields exactly one mocked HTTP call; no fallback instructions or repair. Test best-effort behavior separately if retained.
- [ ] T19: Unsupported route capabilities/settings fail before HTTP; empty tools stay empty; returned tool calls are violations.
- [ ] T20: Refusal, prompt safety block, token-limit truncation, provider failure and malformed completed output remain distinct.
- [ ] T21: Usage survives schema rejection; reasoning tokens are not counted twice; absent counters and unavailable cost remain distinguishable from zero.
- [ ] T22: Artifact sink preserves request/response data, rejects overwrites, never captures credentials, and records partial/error cases.
- [ ] T23: Test requested/resolved identity mismatch, total deadline across attempts, cooperative cancellation and late-result handling.
- [ ] T24: Run existing Model-Turn/provider tests and caller-compatibility tests. Generate protocol schemas from the canonical contract and update conformance docs.

### P2 — Freeze and separately authorize live verification

- [ ] T25: Publish provider capability matrix, thinking-setting semantics, strict evaluation profile and limits; freeze implementation/configuration.
- [ ] T26: Select explicit provider/model routes and budgets. Do not assume CLI aliases are API models or equivalent thinking settings imply equivalent compute.
- [ ] T27: Obtain separate authorization for live probes or evaluation. Count probes separately; no hidden extra requests.
- [ ] T28: After transport conformance, add the gandiva-eval Thinker adapter using frozen prompts/provider schema projections. Keep all historical experiments untouched.
- [ ] T29: Consider production Spartacus backend migration only as a separately authorized task; preserve research dumps and Gandiva/Spartacus ownership.

## Acceptance boundary

A strict evaluation request must make at most one provider request, use native schema enforcement, expose no research tools, separate thinking artifacts from the final answer, preserve available usage and raw transport artifacts, and return an honest termination state. Semantic correctness remains the caller's responsibility.

Acceptance does not establish model quality, equivalent reasoning effort across providers, schema acceptance by every model, reliable financial conclusions or readiness for a reasoning compiler.

## Verification performed for this review

Read-only inspection of the linked Model-Turn implementation and documentation. Prior offline Gemini integration checks confirmed that six frozen case requests preserve schema, prompt and corpus through Thinker's request parser/payload builder. They did not test live generation, final response normalization or audit completeness.

No live provider/model calls, no implementation changes, no production Gandiva/Spartacus changes and no solver invocation were performed for this document. This file records the next implementation work rather than claiming it complete.
