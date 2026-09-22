# Thinker Image Gateway v0.3 Implementation Audit

Date: 2026-08-21  
Repository: `/Users/anjan/monad/thinker`  
Branch: `main`  
HEAD: `dfbe413d9f7187af43bf8a09764ea2ab88777bd4`  
Package version after implementation: `thinker-core 0.3.0`

## 1. Git state before and after

Before implementation, the worktree contained exactly two pre-existing untracked files:

- `docs/CURRENT_SPEC.md`
- `docs/FUTURE_PLAN.md`

Neither file was read as an implementation specification, modified, deleted, staged, or otherwise reinterpreted. The branch and HEAD did not change. After implementation, those two files remain untracked and untouched; the image-gateway files listed below are the only additional changes.

The governing audit was read before modification. Its SHA-256 was mechanically verified as `422a570e8b06ada03263c55735fdaa9a4f3fdb5d6b6a63e2c2783b5aa0c55729`.

## 2. Files added

- `thinker-core/thinker/image_models.py`
- `thinker-core/thinker/image_inspect.py`
- `thinker-core/thinker/image_pricing.py`
- `thinker-core/thinker/image_budget.py`
- `thinker-core/thinker/image_ledger.py`
- `thinker-core/thinker/image_runtime.py`
- `thinker-core/thinker/adapters/image_http.py`
- `thinker-core/thinker/adapters/openai_image.py`
- `thinker-core/thinker/adapters/google_image.py`
- `thinker-core/thinker/adapters/seedream_image.py`
- `thinker-core/thinker/tests/image/test_image_core.py`
- `thinker-core/thinker/tests/image/test_image_adapters.py`
- `THINKER_IMAGE_GATEWAY_V0_3_IMPLEMENTATION_AUDIT.md`

## 3. Files modified

- `spec/registry.yaml`: three exact image-generation call rows.
- `thinker-core/pyproject.toml`: package version `0.2.0` to `0.3.0`.
- `thinker-core/thinker/__init__.py`: stable public exports and version.
- `thinker-core/thinker/core.py`: additive `Thinker.generate_image` entrypoint.
- `thinker-core/thinker/adapters/base.py`: separate image-adapter interface.
- `thinker-core/thinker/adapters/__init__.py`: image adapter registration.
- `thinker-core/thinker/registry/schema.py`: image kind, modality, limits, and price schema.
- `thinker-core/thinker/registry/resolver.py`: exact image-call resolver.
- `thinker-core/thinker/registry/auth.py`: ByteDance Ark environment lookup.
- `thinker-core/thinker/registry/secure_credentials.py`: ByteDance Ark secure lookup/listing.

No Vinci file, ThinkerQL schema, chat adapter behavior, `map_chat`, or existing pricebook behavior was modified.

## 4. Public API

The first-class entrypoint is:

```python
response = thinker.generate_image(request)
```

The stable package exports are `ImageGenerationRequest`, `ImageGenerationResponse`, `ImageOutput`, `AttemptRecord`, `StructuredError`, `CostEstimate`, `CompletionLedger`, `BudgetLedger`, `request_fingerprint`, `estimate_image_request_cost`, and `map_images`.

Image generation is not routed through `chat_ql`; ThinkerQL remains unchanged.

## 5. Typed request and response contracts

`ImageGenerationRequest` is a frozen dataclass with caller-supplied `request_id`, exact `call_id`, prompt, `n`, size, aspect ratio, quality, seed, JSON provider options, opaque string metadata, per-call cost ceiling, unknown-price policy, bounded attempts, and explicit failed-request resume. It rejects empty identities/prompts, invalid counts/budgets, non-string metadata, and non-JSON provider options.

`ImageGenerationResponse` separately preserves request/call identity, registry provider/model identity, provider-returned model and response IDs, registry SHA-256, effective parameters, ordered outputs, usage, cost, latency, every external-call attempt, caller metadata, status, structured error, and replay state.

Each `ImageOutput` contains the original bytes, detected MIME, locally inspected dimensions, index, and SHA-256. Request and response contracts have deterministic dictionary round trips; response binary data uses base64 only for durable serialization, not provider normalization.

## 6. Registry extension

The registry now supports:

```text
kind: image_generation
modality: image_output
```

`ImageLimits` represents supported sizes/aspect ratios, maximum `n`, seed/quality support, supported qualities, and reference-image support. `ImagePrice` represents known/unknown price state, flat per-image, size, quality, size-quality pricing, and prompt-input price. Existing chat rows remain valid without image fields.

Exact image resolution requires an image call ID. Alias and policy routing are not used.

Registry SHA-256 after this implementation: `5883798e4c96599ad388c95d15f0d62fa59121e50fc232c129e3478f4873f531`.

## 7. OpenAI adapter

Registry call:

```text
openai:gpt-image-1-5.image_generation
model_id: gpt-image-1.5
```

The adapter uses `POST /v1/images/generations`, requests the exact registry model, preserves common size/quality settings, permits only a narrow option whitelist, decodes `b64_json`, and retrieves temporary URL output when necessary. It preserves response/model IDs and usage when returned. Tests verify endpoint, model, prompt/parameter payload, binary decoding, URL retrieval, response IDs, and option rejection.

The model and parameter selection follows current [OpenAI Images API documentation](https://platform.openai.com/docs/api-reference/images). No live image request was made. The registry price is deliberately `known: false` because a mechanically frozen preflight price table was not established; budgeted calls therefore fail closed.

## 8. Google adapter

Registry call:

```text
google:gemini-3-1-flash-image.image_generation
model_id: gemini-3.1-flash-image
```

The adapter executes Gemini `generateContent`, requests image response modality, maps normalized aspect ratio and image size into `imageConfig`, extracts inline image bytes, and preserves response/model IDs and usage metadata. It rejects provider options in v0.3 rather than silently dropping them. Tests verify URL, request shape, image configuration, binary decoding, identity fields, and rejection behavior.

The GA model replaces the retired preview identifier and is supported by the current [Gemini image-generation documentation](https://ai.google.dev/gemini-api/docs/generate-content/image-generation) and [model documentation](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-image). Registry per-image prices for 0.5K/1K/2K/4K follow the current [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing). The preflight estimate adds a conservative UTF-8-byte upper bound for prompt tokens. No live request was made.

## 9. Seedream / ByteDance adapter

Registry call:

```text
bytedance:doubao-seedream-4-0-250828.image_generation
model_id: doubao-seedream-4-0-250828
```

The adapter uses the documented Volcano Engine Ark `/api/v3/images/generations` contract, requests `b64_json`, disables sequential generation, accepts only `watermark` as a v0.3 provider option, and retains URL fallback. It preserves response/model IDs and usage where returned. Tests verify endpoint, exact model, payload, byte decoding, response ID, and option mapping.

The contract and model form are grounded in the official [Volcano Engine ImageGenerations API](https://api.volcengine.com/api-docs/view?action=ImageGenerations&serviceCode=ark&version=2024-01-01). Execution was not live-verified. `ARK_API_KEY` is the environment fallback. Price remains `known: false`; budgeted execution fails closed.

## 10. Credential paths

All adapters call Thinker's credential abstraction. OpenAI uses `OPENAI_API_KEY`, Google uses `GOOGLE_API_KEY`, and ByteDance uses `ARK_API_KEY` as environment fallbacks after existing secure storage policy. Provider keys are not request fields, effective parameters, completion rows, logs, or errors.

## 11. Pricing implementation

`estimate_image_request_cost(request, resolved_call)` is pre-provider. It selects an exact size-quality, size, quality, or flat per-image rule, multiplies by `n`, and adds any configured conservative prompt-input bound. It returns maximum estimate, basis, and known/unknown state.

Google has a known size-based registry table. OpenAI and Seedream remain unknown rather than using fabricated precision. Unknown-price requests fail closed by default. An unbudgeted caller may explicitly set `allow_unknown_cost=True`; a run-level budget never reserves unknown cost.

For known calls, `cost_usd` currently reports the preflight maximum estimate and `usage.cost_is_preflight_estimate` records that fact. It is not represented as a provider-billed invoice amount.

## 12. Hard budgets

Per-call `max_cost_usd` is checked before adapter lookup/execution. An over-budget or unknown-price call returns `budget_rejected` without invoking a provider.

`BudgetLedger` provides lock-protected reserve/commit/release operations. `map_images` reserves the known maximum before each call and refuses to launch when remaining budget is insufficient. Tests assert provider non-invocation on both rejection paths.

## 13. Retry policy

The gateway performs bounded exponential backoff with jitter, honors numeric `Retry-After`, and records each attempt. Rate limits, selected 5xx errors, and unambiguous connection failures are retryable. Deterministic 4xx, unsupported parameters, safety failures, invalid responses, budget failures, and adapter errors are not.

Timeout-after-send is marked ambiguous and not retried. This chooses uncertain status over possible duplicate billing. Tests cover 429 recovery, 5xx recovery, nonretryable 4xx, attempt history, and ambiguous timeout behavior.

## 14. Structured errors

The model includes `rate_limit`, `timeout`, `connection`, `provider_5xx`, `provider_4xx`, `safety_rejection`, `invalid_response`, `budget_rejected`, `unsupported_parameter`, `adapter_error`, `idempotency_conflict`, and `unknown`. Records retain retryability, HTTP status, provider code, retry delay, and ambiguous-after-send state without credentials or raw response dumps.

## 15. Durable completion ledger

`CompletionLedger` uses SQLite keyed by `request_id`. A claim is committed before external execution. It stores the canonical request fingerprint, status, complete normalized response metadata, attempt history, caller metadata, prompt SHA through usage, output hashes, and binary outputs.

Binary BLOB retention is intentional in v0.3: it permits exact byte replay without asking the caller to chase a temporary URL or making a second paid generation. Thinker still does not choose Vinci paths or write Vinci experiment files.

## 16. Idempotency semantics

The SHA-256 request fingerprint covers call ID, prompt, `n`, normalized common parameters, provider options, and caller metadata. It excludes operational retry count, cost ceiling, and timestamps.

- Matching completed success: return the exact stored bytes and metadata with `replayed=True`; no provider call.
- Same ID with different fingerprint: `idempotency_conflict`; no provider call.
- Matching completed failure: replay failure by default.
- Matching completed failure with `retry_failed=True`: explicitly reclaim and retry.
- Matching in-progress request: fail closed as an in-progress conflict.

The last rule deliberately requires reconciliation after a process death during an ambiguous external request; it does not blindly regenerate.

## 17. Safe resume

Completed successes are safe to resume and were tested across repeated calls. Failed requests require explicit retry permission. A crash leaving `in_progress` is not automatically retried because provider-side completion may be unknowable. This is duplicate-billing-safe but requires an operator decision for that exceptional state.

## 18. Batch and concurrency

`map_images(thinker, requests, budget_usd=..., max_concurrency=1)` is request-ID based, preserves input order, uses the completion ledger, reserves budget before launch, and returns structured responses for successes and failures. Re-running a completed batch performs ledger replay.

v0.3 is intentionally serial. Passing `max_concurrency` other than 1 fails explicitly. There is no hidden unbounded scheduler.

## 19. MIME, dimensions, and hashes

Local standard-library inspection recognizes PNG, JPEG, and WebP signatures, reads dimensions where supported, and computes SHA-256 over unmodified bytes. Detected MIME overrides an incorrect provider claim. Empty or unrecognized output is `invalid_response`; no decode/re-encode or transcode occurs.

Tests include an intentionally false `image/jpeg` claim over PNG bytes and verify normalized PNG MIME, dimensions, and SHA.

## 20. Provenance fields

Successful responses expose caller request ID, exact call ID, registry-requested model ID, resolved model ID, provider-returned model/version when present, provider response ID, exact registry SHA, effective sent parameters, caller metadata, usage, cost basis, latency, attempts, output index/MIME/dimensions/SHA, and prompt SHA.

Thinker's metrics write is secondary and best-effort. Tests prove an observability failure cannot hide or invalidate the authoritative generation response.

## 21. Tests

An isolated Python 3.12.8 environment was created at `/private/tmp/thinker-image-gateway-venv`. The project was installed using `thinker-core[dev]`, which installed the declared runtime and test dependencies including `jsonschema`; `setuptools` was then installed in that isolated environment for the no-isolation wheel build. Global Python was not modified.

New image-gateway tests: **28 passed**. They cover typed round trips, validation, registry kind/resolution, fingerprints, binary metadata, effective parameters, provenance, opaque metadata, per-call/run budgets, error categories, retries, attempt records, ambiguity handling, completion replay, conflicts, explicit failed resume, batch association/resume/budget, observability isolation, all three mocked HTTP adapters, parameter mappings, response IDs, and price inputs.

Static lint over all new image modules and tests: **PASS**. A targeted mypy invocation could not produce a result because installed mypy 2.3.1 terminated with its own internal error; no mypy result is claimed.

Package build: **PASS**, wheel `thinker_core-0.3.0-py3-none-any.whl`, SHA-256 `ae071a00d0742579ee1e1b79803b276339028a7c960d7fc33c799bab0f3d17cb`.

## 22. Existing regression

The final provider-free aggregate run produced:

```text
131 passed, 1 deselected
```

This consists of all 103 provider-free legacy tests plus 28 new tests. The one pre-existing explicit zero-generation credential check was run separately and passed. Therefore all **104/104 existing tests passed**, and all **28/28 new tests passed**. The credential check may issue a model-list authentication request with an intentionally invalid test key; it did not generate content or incur image-generation cost.

## 23. Known limitations

1. Provider execution is mock-verified only; credentials, account entitlement, exact live response variants, and regional access remain unverified.
2. OpenAI and Seedream registry pricing is unknown, so hard-budget calls fail closed until reviewed prices are frozen.
3. Google `cost_usd` is a conservative preflight estimate, not a reconciled invoice value.
4. Batch execution is serial (`max_concurrency=1`).
5. A crash-stale `in_progress` record requires manual reconciliation; automatic retry could duplicate a billable result.
6. SQLite retains output bytes for exact replay, increasing ledger storage.
7. Local metadata inspection is limited to PNG, JPEG, and WebP.
8. OpenAI/Google provider-side model aliases or silent revisions cannot be prevented; provider-returned model/version is retained when supplied.
9. Seedream is tied to the documented dated model ID and Beijing Ark endpoint default; other regions require explicit endpoint configuration.
10. Multimodal judging, FLUX, Qwen Image, asynchronous execution, and Vinci-specific file writing are outside scope and absent.

## 24. Exact readiness for the Vinci bridge

Vinci can now depend only on Thinker's typed request/response interface and exact call IDs; it need not import provider SDKs or manage credentials. Vinci retains VisualSpec, prompt/hash, experiment metadata, filenames, atomic image writes, and its run manifest. Thinker returns opaque caller metadata together with provider bytes and authoritative call metadata.

The bridge can be implemented and tested against this interface now. Real provider execution should not start until a separate authorization gate verifies credentials/entitlements, freezes OpenAI and Seedream price rules or explicitly accepts unknown cost for a tiny smoke test, and records the resulting live response shapes. The 960-call pilot remains unauthorized and not ready.

=== THINKER IMAGE GATEWAY STATUS ===
COMPLETE

=== THINKER VERSION ===
dfbe413d9f7187af43bf8a09764ea2ab88777bd4
thinker-core 0.3.0

=== PUBLIC IMAGE API ===
Thinker.generate_image(request: ImageGenerationRequest) -> ImageGenerationResponse

=== IMAGE REQUEST CONTRACT ===
Caller request ID, exact registry call ID, prompt, n, size/aspect/quality/seed, validated provider options, opaque caller metadata, hard per-call budget, unknown-price policy, and bounded retry policy.

=== IMAGE RESPONSE CONTRACT ===
Exact request/call/registry/provider provenance, effective parameters, ordered original bytes with MIME/dimensions/SHA-256, usage/cost/latency, all attempts, opaque metadata, and structured status/error.

=== IMAGE REGISTRY KIND ===
PASS

=== OPENAI IMAGE ADAPTER ===
READY
Exact call openai:gpt-image-1-5.image_generation maps to gpt-image-1.5; mocked API contract passes, live entitlement is unverified, and price is fail-closed unknown.

=== GOOGLE IMAGE ADAPTER ===
READY
Exact call google:gemini-3-1-flash-image.image_generation maps to GA gemini-3.1-flash-image; mocked API contract and known size pricing pass, live entitlement is unverified.

=== SEEDREAM IMAGE ADAPTER ===
READY
Exact call bytedance:doubao-seedream-4-0-250828.image_generation uses the documented Ark contract; mocked API contract passes, live regional entitlement is unverified, and price is fail-closed unknown.

=== IMAGE COST ACCOUNTING ===
PARTIAL
Known size-based Google preflight maximums are implemented; OpenAI and Seedream remain unknown and fail closed rather than using fabricated prices.

=== HARD PRE-CALL BUDGET ===
PASS

=== STRUCTURED RETRIES ===
PASS
Bounded 429/selected-5xx/unambiguous-connection retries with backoff, jitter, Retry-After, attempt records, and no ambiguous timeout replay.

=== DURABLE COMPLETION LEDGER ===
PASS

=== SAFE RESUME / IDEMPOTENCY ===
PASS
Completed matching requests replay exact bytes without provider calls; mismatched IDs fail; failed retries require explicit permission; ambiguous in-progress records fail closed.

=== MIME / DIMENSIONS / SHA ===
PASS

=== EXACT CALL / REGISTRY PROVENANCE ===
PASS

=== EXISTING THINKER TESTS ===
104/104 passed (103 in provider-free aggregate plus the single pre-existing zero-generation credential test separately).

=== NEW IMAGE-GATEWAY TESTS ===
28/28 passed.

=== PAID PROVIDER CALLS MADE ===
0

=== READY FOR VINCI BRIDGE ===
YES
The bridge can use the stable typed interface and exact call IDs without provider SDK knowledge; no Vinci-specific semantics were added to Thinker.

=== READY FOR REAL 3-CALL SMOKE TEST ===
NO
Credentials/entitlements and live response variants are unverified, and OpenAI/Seedream hard-budget price entries must be frozen or an explicit smoke-only unknown-cost exception must be authorized first.

=== READY FOR 960-IMAGE PILOT ===
NO
The full pilot requires a separately authorized real-provider smoke test and frozen Vinci execution manifest.
