# Thinker inference architecture results — 2026-10-07

## Outcome

The six architectural goals are implemented as an opt-in extension of Model-Turn V1. No separate application-specific inference API was created. No live model requests, compiler/solver execution, or production Gandiva/Spartacus edits occurred.

| Goal | Delivered | Boundary |
|---|---|---|
| Capability profiles | Host-owned, model-specific NATIVE/EMULATED/UNSUPPORTED profiles with provenance and thinking controls | Concrete profiles still require model qualification; no automatic live discovery |
| Explicit execution planning | Pure planner binds exact route/model, request, schema, prompt, settings, registry and profile snapshot before submission | No routing optimizer or silent substitution |
| Functional adapters | Pure request lowering and provider response/error lifting for OpenAI Responses, Anthropic Messages, Gemini GenerateContent | Initial planned execution is text-only, no tools/continuation/streaming |
| Outcome/accounting/audit | Separate execution and structural states; answer/thought separation; nullable usage/cost; private immutable audit capture | Unknown cost stays unknown; remote cancellation is not asserted |
| Controlled workarounds | Explicit preselected JSON_PROMPT emulation where supported and authorized | Native-required path never downgrades, retries, repairs or substitutes models |
| Conformance scenarios | End-to-end fake-provider and runtime/service acceptance scenarios, followed by complete regression suite | Offline transport conformance does not demonstrate live model quality |

## Functional boundary and compatibility

`plan_turn → lower_request → execute_inference → lift_response` separates pure decisions from network/filesystem effects. The first acceptance scenarios were written against function prototypes and failed before implementation; they then passed with implementation. Tests cover complete execution tasks, including policy rejection before HTTP, response channels, usage, errors, partial transport capture, immutable audit, cancellation and service integration.

The existing Model-Turn V1 endpoint and request model remain. Only requests with `inference_policy` enter the planned path. Legacy callers retain existing behavior. The Gemini parser additionally stops treating thought-marked text as answer text. Canonical Pydantic models generate the checked-in request/response schemas.

## Files and complexity

Five new source modules under `thinker-core/thinker/model_turn/`: `inference_contract.py`, `inference_plan.py`, `inference_adapters.py`, `inference_execution.py`, `inference_runtime.py`.

Existing integration changes: `models.py`, `runtime.py`, `server_cli.py`, `gemini.py`, and the two generated Model-Turn schemas. One new scenario suite: `thinker/tests/model_turn/test_inference_architecture.py`.

New module physical line counts (including comments/blank lines): contract 140, plan 257, adapters 245, execution 236, runtime 125; total 1,003. New scenario suite: 484 physical lines. No new Python dependencies. Documentation: architectural plan, [usage/contract guide](INFERENCE_PLANNING.md), this report, and a link from MODEL_TURN_V1.md. The original gap-observation document is retained.

## Verification

Working directory: `/Users/anjan/monad/thinker/thinker-core`.

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest thinker/tests -p no:cacheprovider --tb=short
```

Result: **214 passed, 2 skipped in 4.72s**. The opt-in real-model tests were skipped. Loopback service tests required local socket permission; no provider calls occurred.

```sh
.venv/bin/python -m ruff check thinker/model_turn/inference_*.py thinker/tests/model_turn/test_inference_architecture.py thinker/model_turn/models.py thinker/model_turn/gemini.py thinker/model_turn/runtime.py thinker/model_turn/server_cli.py --no-cache --output-format concise
```

Result: **All checks passed**.

## Historical integrity

Read-only evaluation integrity verification passed every stored entry: GE0-B 1,554; GE0-C 773; GE1-A files 21 and artifacts 55; GE1-B 1,377; GE2-A 393; GE2-A2 artifacts 423, implementation 16, archive 1 and initial evaluation 133; A2R start 579. These groups overlap and are not a unique-file total. Production manifests matched Gandiva 148/148 and Spartacus 123/123.

GE2-A3P frozen protocol manifest: 80/80 hashes unchanged. No frozen evaluation score, prompt, output or protocol file was modified.

## Remaining work

Select and freeze concrete provider/model routes and reviewed capability profiles, then separately authorize live compatibility qualification. The planned path does not yet provide universal provider-schema compilation, full billing calculation, tool orchestration, vision, or streaming. Application semantic admission remains outside Thinker. Production Spartacus adoption and any new benchmark collection remain separate work.
