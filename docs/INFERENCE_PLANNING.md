# Planned inference in Model-Turn V1

> Qualification update: ordinary live execution now uses the existing ThinkerQL resolver and adapter path with a matching immutable qualification PASS. Default standalone Model-Turn live calls fail closed. See [Qualification](QUALIFICATION.md). The original transport functions remain implementation/test components.

Implemented 2026-10-07. This is an opt-in architectural extension to Thinker's existing stable inference API, not a separate agent runtime or financial reasoning system.

```text
Application intent + caller output schema
    → ModelTurnRequest.inference_policy
    → exact registry route + host capability profile
    → immutable InferencePlan
    → provider request lowering
    → one HTTP attempt under deadline
    → provider response/error lifting
    → ModelTurnResponse + InferenceOutcome
```

## Six architectural goals

| Goal | Implementation |
|---|---|
| Rich capability profiles | `CapabilityProfile`: NATIVE / EMULATED / UNSUPPORTED, model identity, provenance, thinking controls |
| Explicit planning | `plan_turn`: reject incompatible requirements before credentials/HTTP; bind hashes and model |
| Faithful adapters | Pure `lower_request`, provider-specific response functions and `lift_response` |
| Lossless distinctions | Independent termination/structure states, known/unknown usage, final-answer separation and optional raw audit |
| Controlled workarounds | Named JSON_PROMPT selected before execution, only when explicitly permitted; no reactive downgrade |
| Conformance | Complete scenario tests across the three native providers and existing HTTP service |

Applications own semantic correctness. Thinker checks structure and execution policy, not the meaning of a research claim.

## Stable contract and compatibility

The existing `/v1/model-turn` endpoint and ModelTurnRequest/ModelTurnResponse remain. Requests without `inference_policy` use the legacy V1 behavior, including its documented best-effort schema fallback. Requests with the policy use the new planned path. This distinction is deliberate: legacy experiment behavior is not silently changed.

The request schema gains an optional policy. Responses gain optional `inference_plan` and `inference_outcome`; `usage` and `cost` can be null when unknown on the planned path. Existing legacy behavior/tests remain intact. Consumers enabling the new policy must inspect the new outcome dimensions, not equate `normalized_error is None` with completed generation. REFUSAL and INCOMPLETE are not semantic failures.

The common contract is Pydantic; checked-in JSON schemas are regenerated from it. No inference-model schema or provider data structure is hand-maintained separately.

## Request example

This is a template, not a selected or qualified live model:

```json
{
  "protocol_version": "thinker.model-turn.v1",
  "request_id": "unique-application-run-id",
  "requested_route": "gemini:CONCRETE_REGISTRY_CALL.chat",
  "messages": [{"role": "user", "content": "Supplied corpus and question"}],
  "system_instruction": "Application-owned semantic instructions",
  "response_schema": {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": false
  },
  "tools": [],
  "tool_results": [],
  "generation": {"max_output_tokens": 12000},
  "timeout_ms": 1200000,
  "inference_policy": {
    "expected_model": "EXACT_PROVIDER_MODEL_ID",
    "schema_policy": "NATIVE_REQUIRED",
    "max_attempts": 1,
    "tool_policy": "NONE",
    "thinking": {"mode": "provider_default"}
  }
}
```

Host `--inference-profiles profiles.json` maps exact registry call IDs to profiles, for example:

```json
{
  "gemini:CONCRETE_REGISTRY_CALL.chat": {
    "provider": "gemini",
    "model": "EXACT_PROVIDER_MODEL_ID",
    "source": "Reviewed provider documentation and model qualification record",
    "structured_output": "NATIVE",
    "json_mode": "NATIVE",
    "effort_values": ["low", "high"],
    "disable_thinking": false
  }
}
```

Replace the example with verified model-specific capabilities; these settings are not universally valid for Gemini. Profiles are supplied by the host, not asserted by a model response or ordinary request. Existing registry capabilities are checked too. Automatic live capability discovery is not performed. A documented capability remains distinct from successful model qualification.

Unsupported settings, profile/model mismatches, aliases, unsupported endpoints and insufficient output limits fail before any provider submission. The initial planned executor is text-only, no tools, no continuation, no streaming, official native endpoints only. Sampling overrides are rejected rather than silently ignored. Vision/tools/streaming fields in a profile do not imply this executor supports their orchestration.

## Functional implementation

```python
plan_turn(request, registry_call, capability_profile, revision) -> InferencePlan
lower_request(request, plan) -> PreparedInference
lift_response(prepared, http_status, raw_body) -> InferenceOutcome
await execute_inference(prepared, credentials=resolver, send=optional_sender,
                        audit_directory=optional_private_directory,
                        cancel_event=optional_event) -> InferenceOutcome
```

The first three functions are pure. Prepared payloads/request snapshots are immutable bytes. Hashes bind the original request, registry record, capability/settings snapshot, schema, prompt and actual outbound body. The plan exposes the selected enforcement and workaround before execution. No application must construct provider-specific payloads.

The existing synchronous service delegates to these functions through `planned_turn`. Async applications can call `execute_inference` directly; do not invoke synchronous `runtime.turn` inside an already-running event loop. Injected senders/credential resolvers support offline tests without calling providers.

## Provider-specific lowering and thinking

- OpenAI: Responses API, strict `text.format` when native, `reasoning.effort` when selected, `store=false`, no tools. OpenAI's reasoning controls and supported levels are model-specific. [Official reasoning documentation](https://developers.openai.com/api/docs/guides/reasoning).
- Anthropic: Messages API, `output_config.format`, explicit disabled/manual-budget/adaptive thinking. Budget must meet the reviewed model bounds and remain below the output budget; adaptive effort is used only when configured. [Official thinking documentation](https://platform.claude.com/docs/en/build-with-claude/extended-thinking).
- Gemini: GenerateContent, `responseJsonSchema` and JSON MIME type, `thinkingLevel` or `thinkingBudget` according to the selected model profile. [Official thinking documentation](https://ai.google.dev/gemini-api/docs/generate-content/thinking).

No arbitrary provider override dictionary bypasses the typed controls. `provider_default` means the effective internal budget is unknown. Explicit settings record what was submitted, not an attestation of actual internal model computation. No equivalence of effort or quality across providers is claimed.

## Native versus emulated structure

NATIVE_REQUIRED never retries or falls back. Provider rejection remains a visible failed attempt. ALLOW_EMULATION can preselect the named JSON_PROMPT workaround for an OpenAI JSON-mode route when native schema support is unavailable and the profile permits it. The plan records EMULATED; instructions add the schema and output is locally validated. This is not constrained decoding. There is no automatic repair, validation loop, model substitution or retry. Other emulations are currently unsupported.

## Response channels and accounting

Final-answer text is separate from reasoning/thinking blocks and opaque signatures. The normalized outcome carries artifact counts, not thought transcripts. Raw provider envelopes may include provider-exposed thinking artifacts when audit capture is explicitly enabled. No hidden chain-of-thought is requested. No thought content becomes application evidence automatically.

The Gemini V1 parser also now excludes `thought: true` text from `assistant_content`, preserving thought flags in any required continuation replay. Existing tool-continuation signatures remain opaque.

Execution states: COMPLETED, REFUSAL, INCOMPLETE, PROVIDER_ERROR, TIMED_OUT, CANCELLED, CONFIGURATION_ERROR.

Structural states: VALID, INVALID, NOT_EVALUATED. A completed schema-invalid output keeps its answer, usage and raw artifacts; its `structured_output` is absent. A refusal/truncation does not become a JSON validity judgment. Unexpected tool/nontext/grounding output and resolved-model mismatches are explicit errors.

`inference_outcome.usage` is authoritative for the planned path: nullable counters and raw provider-reported values preserve unknowns. OpenAI output already includes reasoning; Gemini visible and thought counts are combined only when both exist. Anthropic cache details and original totals remain in raw usage. Legacy `usage`, when derivable, is a compatibility view with its existing total convention. Known usage survives schema failures. Cost is null/UNKNOWN: no unsupported billing estimate is invented. Accurate pricing including cache tiers is future work.

## Deadlines, attempts and audit

One async HTTP attempt, no redirect following and no transport retries. A wall deadline covers execution and parsing; HTTP timeouts also bound I/O. Cancellation cancels the local async operation, but remote cancellation is never asserted. Injectable senders must honor async cancellation. If transport exposes partial bytes before an error, they are retained separately without fabricating a valid answer. Bytes unavailable to the caller cannot be reconstructed.

Audit is optional and disabled by default. The server accepts `--inference-audit-root`; each planned call gets a host-generated private run directory. Clients cannot select file paths. Files are created exclusively and cannot overwrite an earlier run. The low-level function also rejects an existing directory before HTTP.

Artifacts include identity, plan, canonical request, actual provider request, attempt metadata, raw response (or available partial bytes), capture hashes/redaction metadata and final outcome. Credentials are resolved only at execution and supplied only in HTTP headers; headers are not archived. Exact credential echoes are redacted from stored bytes with an explicit marker and original/stored hashes. Ordinary telemetry remains content-free. The operator owns audit retention and access; provider outputs can contain sensitive application data.

For service startup:

```sh
thinker-model-turn-server --registry spec/registry.yaml \
  --revision <exact-source-revision> \
  --inference-profiles /path/to/reviewed-profiles.json \
  --inference-audit-root /path/to/private-audits
```

## Limits and next gate

This implementation qualifies transport behavior with fake providers, not live models. No concrete production routes have been selected. Full generic schema-compatibility compilation is not implemented: provider schema rejection is captured without weakening the request. Schema projection remains caller-owned; Pydantic/jsonschema do local structural checks, and semantic admission remains in Gandiva.

Before live GE2-A3: select/freeze concrete models and profiles, enable strict policy and audit, verify the frozen application schemas can be submitted, and authorize any live compatibility probes separately. Production Spartacus migration and a ReasoningSpec compiler remain separate tasks.
