# Frozen application-contract qualification

Implemented 2026-10-07. Qualification extends the existing path:

```text
ThinkerQL → registry resolver → qualification gate → adapter → execution record
                                   ↑                          ↓
                        immutable current decision ← external application validator
```

Thinker validates execution policy, structure, identity and artifact integrity. Applications own their semantics. The first application is `gandiva-eval`; PIT, evidence and financial rules remain in Gandiva/Spartacus. There is no Thinker finance ontology, gold-answer scorer, compiler or reasoning engine.

## Identity and applicability

A qualification key binds all of:

- application profile ID and contract version;
- complete frozen contract hash, including prompts, schemas, cases, repetitions, validator command and artifact checksums;
- resolved provider, exact model, registry call, adapter and payload style;
- host adapter configuration, including capability and thinking configuration;
- Thinker implementation hash and Python/dependency versions.

The registry resolver selects the current qualification for the **resolved target**, not a provider-wide or model-family badge. Alias routing cannot bypass this check. There is no fallback to another model or an older PASS. Code, configuration, model or contract changes invalidate applicability. External application artifacts and their declared runtime metadata are rechecked before admission.

The initial profile is deliberately narrow: six frozen `gandiva-eval` inputs, five independent slots each, with exact instructions. It does **not** qualify arbitrary production investigations. A broader production contract needs its own version, cases and qualification evidence.

## Immutable evidence

```text
records/<decision SHA256>.json
    target identity
    previous_record
    status, mode, recorded_at
    slots: case ID, repeat, execution_ref
                ↓
evidence/<execution-manifest SHA256>.json
    normalized stage outcome
    artifact-name → raw-byte SHA256
                ↓
blobs/<raw-byte SHA256>.bin
```

Decisions, execution manifests and payload blobs are content-addressed. Atomic exclusive publication never overwrites a previous file. A later qualification appends a new decision, linked to the previous live decision for the same identity. Local append locking prevents simultaneous writers from creating ambiguous heads. The resolver follows that chain and checks the latest record and all referenced execution evidence. A failed or review-required requalification supersedes applicability of an earlier PASS; it does not delete it.

Every live-probe slot references its precise request, provider request/response and available audit stages, normalized outcome, external validator input/stdout/stderr/result, and execution identity. Refusals, incomplete responses, provider failures and failed validators retain the stages that exist. Missing artifacts are not invented. Working directories under `executions/` are staging copies; content-addressed manifests and blobs are authoritative. No mutable `current_pass.json` exists.

Hash verification detects changed bytes, invalid references and broken/forked chains. This is a host-owned evidence store, not a defense against a malicious machine administrator who can replace code or delete the entire store. Configure private storage, backups and retention accordingly. There are no signing keys or distributed ledger dependencies.

## Gate and bootstrap

Ordinary `Thinker.chat_ql` model execution requires a matching current PASS. Without a configured profile/store, model calls fail before credentials or HTTP. Deterministic local echo remains usable.

Qualification probes are the necessary bootstrap exception: only `run_suite(..., mode='LIVE_PROBE', authorize_target=<exact key>)` can initiate the frozen slots for an unqualified target. An ordinary request cannot set a JSON flag to bypass admission. The authorizing host must select the exact key after inspecting preflight. Probe authorization is not an ordinary-execution grant.

`REPLAY` is offline. Even perfect replay fixtures produce `OFFLINE_PASS`, never a live PASS. Replay decisions are retained separately and neither grant nor revoke a live qualification.

The current gate requires all scheduled slots to complete, satisfy the generic structural/execution contract, and receive application `PASS`. Application `REVIEW` produces `REVIEW_REQUIRED`, not FAIL, but does not authorize ordinary execution. A future adjudication policy belongs to the application and must be versioned; Thinker has no implicit review override.

There is one attempt per slot, no retries, no JSON repair, no relation repair, no model substitution, and no autoshrink of frozen input. Contract drift during collection stops further submissions; remaining slots are recorded as unexecuted and the decision cannot pass.

## Existing adapters and Model-Turn

`adapters/planned.py` is a thin ThinkerQL adapter over the native functions already implemented in `model_turn`. It contains no provider HTTP serialization or financial semantics. A qualified registry entry uses `payload_style: model_turn_v1` and its existing provider adapter name. The legacy Chat Completions JSON-mode adapter cannot masquerade as native structured output and fails qualification preflight.

OpenAI, Anthropic and Gemini native functions are reusable transports. The Gandiva frozen profile currently exports only OpenAI and Anthropic projections, because those are the frozen GE2-A3P providers. Gemini needs an explicitly versioned application profile; no substitution is performed here.

The default standalone Model-Turn runtime now refuses live targets and directs callers to qualified ThinkerQL. Fake routes and explicit host-injected test transports remain available for offline conformance. Low-level Python transports and injected factories are trusted implementation/test seams, not application authorization APIs; they cannot mint a qualification PASS outside the runner. No live access flag is exposed through request JSON.

## Application boundary

A host supplies a frozen JSON profile with:

- `profile_id`, `contract_version`, `provider`, `repeats`;
- public case templates in ThinkerQL and application context;
- generic execution requirements;
- an external validator argv, timeout and artifact checksums.

The validator reads one JSON input from stdin and returns JSON containing `status: PASS | FAIL | REVIEW` and `diagnostics: []`. Additional application-owned results are archived unchanged. Thinker does not import the application package. The subprocess receives a minimal environment without provider credentials, a bounded timeout, and process-group cleanup. The host trusts this validator code; it is not a sandbox for arbitrary model-generated programs.

For Gandiva, the external module is `gandiva_eval.qualification`. It exports unchanged public GE2-A3P prompts and provider schemas; only the external validation step reads hidden arithmetic/provenance requirements. It invokes the frozen canonical parser and semantic validator, preserving ACCEPT/REVIEW_REQUIRED/REJECT. It does not expose gold, oracle or scorer details to models. It adds no PIT or financial rules to Thinker.

## Usage

Export using Gandiva's own Python environment:

```sh
PYTHONPATH=src <gandiva-eval-python> -m gandiva_eval.qualification export \
  --provider openai --output /private/contracts/gandiva-openai.json
```

Exports use exclusive creation and bind the actual application interpreter, validator source, frozen sources and dependency metadata. Use an environment intended for qualification; moving/upgrading it requires a fresh profile export and qualification.

Create a reviewed host configuration file (values below are placeholders, not qualified models):

```json
{
  "capability_profile": {
    "provider": "openai",
    "model": "EXACT_CONCRETE_MODEL_ID",
    "source": "Reviewed model documentation and host configuration",
    "structured_output": "NATIVE"
  }
}
```

Inspect without provider calls:

```sh
thinker qualify inspect --registry registry.yaml \
  --profile /private/contracts/gandiva-openai.json \
  --configuration /private/contracts/openai-config.json \
  --records /private/qualification-evidence --call <exact-registry-call>
```

`thinker qualify status` with the same arguments resolves and verifies the current PASS. `thinker qualify replay --fixtures fixtures.json` runs an offline suite; fixtures map `case-id/repeat` to normalized outcomes. The separately authorized live command is `thinker qualify run ... --authorize-target <inspected-key>`. It has not been run against any provider in this task.

Ordinary execution uses the existing `chat-ql` command with `--qualification-profile`, `--qualification-records`, and `--qualification-config`, or equivalent constructor parameters on `Thinker`. `map-chat-ql` accepts the same host settings. Responses expose the qualification record reference and application outcome. Native `execution` metadata is authoritative for known/unknown usage; legacy top-level token/cost fields remain compatibility estimates.

## Limits

A qualification certifies only its declared contract and observed probe evidence. Model behavior is stochastic; future requests can still fail application validation. Server-side changes behind a provider model ID are not detectable from a local code hash alone and may warrant requalification. The framework does not infer financial correctness, perform human adjudication, or turn REVIEW into PASS. No live provider/model has been qualified by this implementation task.
