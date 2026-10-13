# Thinker Python Library User Manual

ThinkerQL 0.4 lets an application send a model request and receive a result packet
through `thinker.model_call`. The application chooses the model, supplies the
conversation, and decides what to do with the answer. Thinker translates the
packet into a native provider request, makes one attempt, and returns content,
tool proposals, usage, or an error.

This manual covers the new Python library path. It needs no Thinker HTTP service.
Automatic routing and local-assistance orchestration are deferred. Legacy APIs
remain available, but their behavior differs; see [Migration](#migration).
For the native Go interface, goroutines, and context cancellation, see the
[Go user manual](GO_USER_MANUAL.md).

## Contents

- [Installation](#installation)
- [First model call](#first-model-call)
- [Function arguments and defaults](#function-arguments-and-defaults)
- [Request packets](#request-packets)
- [Optional output constraints](#optional-output-constraints)
- [Provider protocols and options](#provider-protocols-and-options)
- [Result packets](#result-packets)
- [Tools and conversation state](#tools-and-conversation-state)
- [Errors and recovery](#errors-and-recovery)
- [Concurrency and cancellation](#concurrency-and-cancellation)
- [Metrics and debug traces](#metrics-and-debug-traces)
- [Cloud storage and retrieval](#cloud-storage-and-retrieval)
- [Migration](#migration)
- [Troubleshooting and testing](#troubleshooting-and-testing)

## Installation

Use Python 3.11 or newer. From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ./thinker-core
```

The packet version is `0.4`; it is separate from the distribution's package
version. The new packet schemas ship with the package. Installing the library
does not start a server.

Credentials normally come from the provider's environment variable, then the
existing Thinker credential store. Common variables are `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `GEMINI_API_KEY` or `GOOGLE_API_KEY`, and
`OPENROUTER_API_KEY`. The full mapping is in
[`http_common.py`](../thinker-core/thinker/model_turn/http_common.py).
Alternatively, supply a synchronous credential resolver as a function argument.
Keep credentials outside the request packet.

## First model call

Choose an exact chat route from your existing catalog. `route` is an explicit
provider/model configuration; passing it does not invoke automatic routing.
For this example, set `THINKER_CALL_ID` to a call ID in your catalog and configure
that provider's credentials before running the script from the repository root:

```python
import asyncio
import os
from uuid import uuid4

from thinker import model_call
from thinker.registry.loader import load_catalog

catalog, catalog_sha256 = load_catalog("spec/registry.yaml")
call_id = os.environ["THINKER_CALL_ID"]
route = next((item for item in catalog.calls if item.call_id == call_id), None)
if route is None:
    raise ValueError(f"No catalog entry for {call_id}")
if route.kind != "chat":
    raise ValueError("model_call currently executes chat requests only")

packet = {
    "version": "0.4",
    "request_id": str(uuid4()),
    "job_id": "example-job",
    "turn_id": "turn-1",
    "intent": "chat",
    "messages": [
        {"role": "user", "parts": [{"type": "text", "text": "Explain binary search."}]}
    ],
    "instructions": {"max_output_tokens": 512},
    "timeout_ms": 30000,
}

result = asyncio.run(model_call(packet, route=route, metrics=False))
print(result["status"])
print(result.get("content"))
if result.get("error"):
    print(result["error"]["code"])
```

This disables metrics for the call and uses the default `debug=False` with no
recorder, so it writes neither metrics nor traces. Credential-store lookup may
still access local configuration. In an application that already has an event
loop, use `await model_call(...)` instead of `asyncio.run(...)`.

For a reproducible configuration, keep the catalog hash, exact route, packet,
and application policy together. Thinker records the resolved route in its result,
but it does not automatically attach your catalog hash or deployment configuration.

## Function arguments and defaults

```python
result = await model_call(
    packet,
    route=route,
    # credentials=your_sync_resolver,
    # client=your_httpx_async_client,
    recorder=None,
    debug=False,
    metrics=True,
)
```

| Argument | Meaning and default |
| --- | --- |
| `packet` | ThinkerQL 0.4 request dictionary. Copied and checked for envelope validity. |
| `route` | Required `RegistryCall` selected by the caller. |
| `credentials` | Synchronous function receiving the provider name and returning a key or `None`. Defaults to environment and credential-store lookup; runs off the event loop. |
| `client` | Optional caller-owned `httpx.AsyncClient`. Thinker leaves it open. Otherwise a client is created and closed for the call. |
| `recorder` | Optional `TraceRecorder`. Omitted by default. Explicitly supplying one enables recording. |
| `debug` | `False` by default. `True` records redacted request and response snapshots. |
| `metrics` | `True` uses the existing collector if enabled in configuration. `False` disables collection; a collector object can be supplied. |

The default HTTP client disables retries, redirects, and environment-derived HTTP
settings (`trust_env=False`). If you need a proxy or a specific TLS configuration,
supply a configured client. Disable retries in that client's transport to preserve
one provider attempt per library call. Caller-provided clients and credential
resolvers must be safe for the concurrency pattern you use.

## Request packets

| Field | Behavior |
| --- | --- |
| `version` | Required, exactly `"0.4"`. |
| `request_id` | Required nonempty string identifying the request. Use a fresh ID for each attempt. |
| `intent` | Required. The execution function currently supports `"chat"` only. |
| `messages` | Required transcript array. Roles are `system`, `user`, `assistant`, and `tool`. |
| `job_id`, `turn_id` | Optional correlation IDs. They do not cause history to be loaded. |
| `instructions` | Optional generation settings: `max_output_tokens`, `temperature`, `top_p`, `seed`, `stop`, `json_schema`, and `json_schema_strict`. |
| `tools` | Optional tool names, descriptions, input schemas, and native `strict` preferences. Thinker does not run tools. |
| `tool_results` | New tool results appended after the transcript, in array order. |
| `timeout_ms` | Positive deadline in milliseconds; defaults to 30000 for credential lookup and HTTP execution. |
| `routing` | Optional route consistency pins and provider-specific reasoning settings. No automatic selection is performed. |
| `provider_options` | Native provider settings not already expressed through mapped common fields. |
| `continuation` | Currently supports Responses `{"response_id": "..."}`. |
| `metadata` | Caller extensions. The caller retains these; arbitrary request metadata is not automatically copied into the result. |

Without `instructions.max_output_tokens`, the adapter sends a limit of 256.
Set an appropriate value explicitly for longer answers. Limits and capability
labels in the registry are configuration metadata; this direct function does not
perform capability admission, enforce registry QPS, or truncate context to fit.

Text parts use `{"type": "text", "text": "..."}`. OpenAI-compatible Chat and
Responses paths also map image URL parts. Other media can use native parts:

```python
native_part = {
    "type": "provider",
    "provider": route.provider,
    "data": {"your_native_content_field": "provider-specific value"},
}
```

This is an escape hatch, not a portable media schema. The part's provider must
match the selected route. Native reasoning items, signatures, and tool fields
must be replayed according to that provider's protocol.

For early envelope checks, use `thinker.thinkerql.parse_request(packet)`.
It returns an independent copy or raises `ValueError`. By contrast, `model_call`
normally returns an error packet for an invalid request. Both paths check JSON
representability and envelope shape, not whether the output schema is supported
by a particular model. Unknown top-level fields are rejected; use `metadata` or
the appropriate native extension field.

## Optional output constraints

The caller owns its output vocabulary, schema, and choice of compatible model.
Omitting constraints requests an ordinary response. Putting a format instruction
in prompt text does not itself set the native API's structured-output parameters.
To request the native feature, add `instructions.json_schema`:

```python
decision_schema = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["accept", "revise", "ask"]},
        "explanation": {"type": "string"},
    },
    "required": ["decision", "explanation"],
    "additionalProperties": False,
}
packet["instructions"] = {
    "max_output_tokens": 512,
    "json_schema": decision_schema,
}
```

Thinker forwards the schema, including the enum, unchanged inside the provider's
native format configuration. It does not select a model for this vocabulary,
rewrite unsupported schema keywords, validate the returned answer against the
schema, or discard an imperfect answer.

| Selected protocol | Native output setting |
| --- | --- |
| Chat Completions | `response_format.json_schema` |
| Responses | `text.format` with `type: json_schema` |
| Anthropic Messages | `output_config.format` with `type: json_schema` |
| Gemini GenerateContent | `generationConfig.responseJsonSchema` and JSON MIME type |

For Chat Completions and Responses, the caller can additionally set:

```python
packet["instructions"]["json_schema_strict"] = True
```

`True` and `False` are passed as the native `strict` value. If omitted, no `strict`
field is added; the selected provider determines its default behavior. This flag
requires `json_schema`. It is not mapped for Anthropic or Gemini, so explicitly
setting it on those routes returns `UNSUPPORTED_REQUEST` before a network call.
Those protocols still accept the common `json_schema` setting. Omission does not
mean a provider disables its own schema enforcement.

This changes the earlier 0.4 implementation, which automatically added
`strict=True` on Chat Completions and Responses whenever a schema was present.
To preserve that request behavior, now set `json_schema_strict=True` explicitly.
Tool declaration `strict` is a separate setting; it is also currently mapped
only for Chat Completions and Responses.

An unsupported constraint may be rejected by the provider. Thinker returns that
error without retrying or dropping the constraint. If a provider instead returns
malformed JSON or an out-of-vocabulary answer, the content remains available.
`completed` means the call completed, not that the application accepted the answer.

The application can parse and validate content, accept a useful partial answer,
or send a bounded correction request with a fresh request ID. For a plain text
exchange without tools or opaque reasoning, a correction can append the original
assistant text followed by a user message explaining the exact mismatch. Keep
the schema if you still want it applied; remove it only as an explicit host choice.

## Provider protocols and options

| Route `payload_style` | Transport mapping |
| --- | --- |
| `chat_completions_v1` | OpenAI-compatible Chat Completions |
| `responses_v1`, `openai_responses_v1` | Responses |
| `messages_v1` | Anthropic Messages |
| `generateContent_v1beta` | Gemini GenerateContent |
| `model_turn_v1` | Responses, Messages, or GenerateContent for the corresponding `openai`, `anthropic`, or `gemini` adapter identity |

Compatible third-party providers need the correct endpoint and protocol in their
route. Support for a protocol does not establish that every model on that endpoint
supports tools, images, reasoning, or schemas.

Use `provider_options` for supported native settings such as tool choice or
multiple-candidate options. These options cannot replace model identity,
transcript, tool declarations, system messages, or continuation. A native option
that duplicates a common mapped setting is rejected rather than silently winning.
The check is at the top-level native field: for example, Gemini's generated
`generationConfig` cannot be replaced wholesale through `provider_options`.

`routing.reasoning` forwards provider-specific reasoning settings. The caller
supplies the native shape; Thinker does not translate one provider's effort levels
into another's. `routing.route_id`, `model` (`provider/model_id`), or `alias`, if
present, must agree with the explicit route. Only one identity selector is allowed.
Difficulty hints do not select a model in this implementation.

The transport buffers a complete HTTP body. SSE event streaming is not implemented,
and `provider_options.stream=True` is rejected. Embedding, OCR, top-level document
execution, native MLX/Ollama, and `local_mode=only` or `assist` are not wired here.
An existing OpenAI-compatible local HTTP endpoint can be called using a route with
adapter `openai_compatible_local`; that adapter permits missing credentials.

## Result packets

| Field | Interpretation |
| --- | --- |
| `status` | `completed`, `incomplete`, `refused`, `failed`, or `cancelled`. |
| `content` | Generated answer, often a string. It may contain invalid JSON or fail your schema. |
| `tool_calls` | Tool proposals with IDs, names, raw arguments, and provider data. |
| `provider_data` | Native response or error information, including unnormalized alternatives. |
| `error` | Classified error with code, message, and available HTTP/provider/retry details. |
| `usage` | Reported token counts and native usage. Missing counts remain `None`. |
| `cost_usd` | Unknown is `None`; this path has no price estimator. |
| `resolved_route` | Provider, model, and settings. A reported model name can replace the configured name. |
| `invocations` | One direct attempt, or an empty list for failures before transport. |
| `started_at`, `completed_at`, `duration_ms` | Model-call timing; excludes subsequent observation persistence. |
| `metadata.observability` | Invocation ID, metrics/trace receipts, and observation duration. |

Refusal and incompleteness can carry content without an `error` object. Check
`status` as well as `error`. A malformed provider envelope is a `PROTOCOL_ERROR`;
malformed JSON inside valid generated text is preserved as content.

Multiple tool calls are retained. When native options request multiple choices,
the convenience fields describe the first choice; all alternatives stay in
`provider_data`. Native options for multiple outputs vary by provider and model.
Thinker does not turn them into concurrent model requests automatically.

The invocation and top-level usage describe the same direct attempt. Do not add
them together. Interrupted HTTP bodies retain exact received bytes as base64 in
`provider_data.raw_body_base64`; these are partial wire bytes, not necessarily a
complete answer. Unknown token counts and costs must not be interpreted as zero.

## Tools and conversation state

Declare tools using their names and input schemas. Thinker forwards declarations
and returns proposals; the host handles argument parsing, permissions, sandboxing,
execution, and limits. Missing names/IDs or malformed arguments remain visible.
They do not authorize executing an invalid proposal.

A tool result identifies its proposal by `call_id`. Append the assistant's original
proposal, then provide results in either tool messages or top-level `tool_results`.
Do not put the same result in both places. The following fragment illustrates
replaying a failed tool proposal; preserve any additional native reasoning or
signature parts required by your chosen protocol:

```python
from copy import deepcopy
from uuid import uuid4

followup = deepcopy(packet)
followup["request_id"] = str(uuid4())
followup["messages"].append({
    "role": "assistant",
    "tool_calls": result["tool_calls"],
})
followup["tool_results"] = [{
    "call_id": result["tool_calls"][0]["call_id"],
    "output": "Invalid arguments: path must be a string. Please correct this call.",
    "is_error": True,
}]
```

Only use this when the proposal has a usable call ID. Otherwise the host must
construct a valid corrective message for the protocol. In a real transcript,
also preserve any accompanying assistant text and earlier tool results. Top-level
`tool_results` is for the new results of that request; move older results into the
transcript when building subsequent requests.

The host can execute independent tools concurrently and collect results with
their original IDs. Thinker does not schedule tools or infer whether dependencies
allow parallel execution. A question for a human is likewise returned content or
an application-defined tool proposal; the application presents it, waits for the
answer, and includes that answer in a subsequent packet.

Thinker stores no conversation memory between calls. For Responses, the host can
pass the returned `continuation` object with a subsequent incremental request
when appropriate. Avoid duplicating the same historical messages through both
continuation and replay. For other protocols, the host replays the required
transcript and native state explicitly.

## Errors and recovery

`error.retryable` is a hint, not an automatic action. Thinker does not retry,
refresh credentials, switch providers, or correct a prompt on its own. The host
owns retry budgets, backoff, deadlines, and the decision to preserve or replace
partial content.

| Condition | Typical code | Host decision |
| --- | --- | --- |
| Network failure or broken socket before output | `CONNECTION_ERROR` | Retry within the job budget if appropriate. |
| Certificate validation failure | `TLS_ERROR` | Fix connection configuration. |
| Timeout, including HTTP 408 or 504 | `TIMEOUT` | Check partial output and remaining time before retrying. |
| HTTP 409 | `CONFLICT` | Resolve the conflict according to provider semantics. |
| HTTP 429 | `RATE_LIMITED` | Consider `retry_after_seconds` and caller backoff. |
| Quota or billing exhaustion | `QUOTA_EXHAUSTED` | Resolve account limits or explicitly choose another route. |
| HTTP 401 or credential-related 403 | `AUTHENTICATION_FAILED` | Correct credentials. |
| Other HTTP 403 | `ACCESS_DENIED` or a more specific native category | Check permissions or provider restrictions. |
| HTTP 5xx, including 529 | `PROVIDER_ERROR` or `PROVIDER_OVERLOADED`; 504 maps to timeout | Decide whether a bounded retry is worthwhile. |
| HTTP 400 | `REQUEST_REJECTED` or a specific native category | Fix the request instead of blindly replaying it. |
| Context limit | `CONTEXT_LIMIT` | Host edits context or selects a suitable model. |
| User cancellation | `CANCELLED` | End or pause host work according to user intent. |
| Disconnect after response bytes arrive | `BROKEN_STREAM` | Preserve bytes; decide whether to retry. |
| Invalid provider response envelope | `PROTOCOL_ERROR` | Inspect native data and endpoint configuration. |
| Unmapped feature in the adapter | `UNSUPPORTED_REQUEST` | Change the packet or explicitly choose another path. |

Native codes and messages refine HTTP status categories; for example, not every
429 is a transient rate limit. The classifier covers the inspected Hermes error
categories plus transport and packet errors; the full mapping and limitations
are in [the packet reference](THINKERQL_V0_4.md#errors-and-host-recovery).

If implementing recovery, use a fresh request ID per attempt, preserve the job/turn
IDs, and count every attempt against the host's budget. Do not automatically retry
refusals or cancellations. Admission, correctness checks, and any assurance-kernel
submission remain separate host responsibilities.

## Concurrency and cancellation

Independent calls can overlap using `asyncio.gather`. A shared client can pool
connections within one event loop. In this example, the host limits concurrency;
the limit is illustrative, not a recommended provider capacity:

```python
import asyncio
import httpx
from thinker import model_call

async def run_many(packets, route, limit=4):
    semaphore = asyncio.Semaphore(limit)
    async with httpx.AsyncClient(trust_env=False) as client:
        async def run_one(packet):
            async with semaphore:
                return await model_call(packet, route=route, client=client, metrics=False)

        return await asyncio.gather(*(run_one(packet) for packet in packets))
```

`gather` returns results in input order; result IDs identify them independently.
Synchronous worker threads can each use `asyncio.run(model_call(...))`, with a
separate client per event loop. Do not share an `AsyncClient` across loops. Configure
shared credential callbacks, metrics collectors, and storage clients before
starting concurrent work, and do not mutate them while requests run.

Cancel an active task with `task.cancel()`. Once the coroutine is executing, it
normally catches cancellation, aborts its transport, and returns a `cancelled`
packet. This deliberately differs from coroutines that re-raise `CancelledError`;
the host must inspect the returned status. A task cancelled before its coroutine
starts cannot return a packet. Cancelling a synchronous credential or storage
worker does not terminate the underlying thread.

The default 30-second call deadline covers credential lookup and HTTP activity.
It does not include trace uploads, metrics persistence, host tool execution, or
the overall job. Use a separate host budget for end-to-end work.

## Metrics and debug traces

Tracing is off by default. Metrics and tracing are separate controls:

| Invocation settings | Stored observations |
| --- | --- |
| Defaults | Existing scalar metrics if enabled; no trace snapshot. |
| `metrics=False` | No metrics; no trace snapshot unless explicitly enabled. |
| `recorder=recorder, debug=False` | Summary snapshot without prompt/response bodies. |
| `debug=True` | Full redacted snapshot; default local recorder if none supplied. |

To record a full local snapshot:

```python
from thinker.observability import TraceRecorder

recorder = TraceRecorder.local("./thinker-traces")
result = await model_call(packet, route=route, recorder=recorder, debug=True)
receipt = result["metadata"]["observability"]
if receipt["trace"]["status"] == "recorded":
    print(receipt["trace"]["uri"])
```

Without an explicit recorder, `debug=True` uses `THINKER_TRACE_DIR` or
`~/.thinker/traces`. A full snapshot includes the input packet, prepared native
request, captured HTTP headers and bodies, final model result, correlation IDs,
status, usage, and timing. It records what the provider returns, not private
reasoning that the provider does not expose. An early connection failure can have
only the prepared request rather than captured request bytes.

Snapshots are written at call completion. Process termination may prevent a final
snapshot. The saved response precedes the addition of its recording receipt.
Job and turn IDs group calls; the host still records whether its overall job
completed. Recording never loads previous snapshots into model prompts.

Credential fields, known provider keys, authorization headers, cookies, and common
credential encodings are redacted. Raw bodies are redacted before base64 storage;
their original byte hashes identify the pre-redaction data. Debug mode still
stores application content. Use `TraceRecorder(..., extra_secrets=(...),
redactor=your_function)` for application-specific removal. Returned packets are
not redacted by the recorder.

Metrics reuse the existing SQLite collector and dashboard. They store scalar
usage, timing, route, outcome, and correlation IDs rather than prompts or responses.
Environment configuration is read through the shared configuration object; set it
before first use:

| Variable | Default |
| --- | --- |
| `THINKER_ENABLE_METRICS` | `true`; other values disable it. |
| `THINKER_METRICS_DB` | `~/.thinker/metrics.db` |
| `THINKER_TRACE_DIR` | `~/.thinker/traces` when implicit debug recording is enabled. |
| `THINKER_CREDENTIALS_STORAGE` | `auto` |
| `THINKER_KEYSTORE_PATH` | `~/.thinker/keystore.encrypted` |

Missing token counts and costs remain NULL. Aggregate totals are unknown if any
included row lacks the corresponding measurement. The existing database is
migrated transactionally to preserve older rows and add correlation columns.
No OpenTelemetry exporter or dedicated trace viewer is included.

Persistence runs off the event loop but is awaited, so storage adds return latency.
For minimum persistence overhead, omit the recorder, leave debug off, and set
`metrics=False`. Storage failures are reported under `metadata.observability`
without replacing the model result. Receipt statuses are `recorded`, `disabled`,
`failed`, or `interrupted`. An interrupted worker may still finish its write.

## Cloud storage and retrieval

Recorders accept existing SDK clients. The core library does not install cloud
SDKs, create buckets, or discover cloud credentials for you:

```python
from thinker.observability import TraceRecorder, S3TraceStore, GCSTraceStore

# s3_client and gcs_client are already configured by the application.
s3_recorder = TraceRecorder(S3TraceStore(s3_client, "my-bucket", prefix="thinker"))
gcs_recorder = TraceRecorder(
    GCSTraceStore(gcs_client.bucket("my-bucket"), prefix="thinker", timeout=30)
)
```

Pass the chosen recorder to `model_call`; no upload happens just by constructing
it. Cloud clients own authentication and connection settings. S3 uses the supplied
client's retry policy. GCS uploads disable SDK retries and use the configured
timeout. Both request create-only object writes; local publication is atomic and
also refuses overwrites. Cloud adapters have mock-client coverage, not a live
storage qualification in this branch.

Snapshots use this layout, with a configured prefix for cloud stores:

```text
jobs/<sha256(job_id)>/turns/<sha256(turn_id)>/<invocation_id>.json
```

Missing job or turn IDs fall back to `request_id` for their respective hash. The
original IDs remain inside the snapshot. Store the receipt URI with the host's
job record, or list the corresponding hashed prefix later. Thinker does not add
a separate job index or event ledger.

To read a local snapshot:

```python
import json
from pathlib import Path

snapshot = json.loads(Path(receipt["trace"]["uri"]).read_text())
```

For cloud snapshots, retrieve the `s3://` or `gs://` receipt URI with your SDK or
cloud tooling and decode the downloaded JSON. Correlate `ids`, inspect `status`
and `error_code`, then compare `request`, `native_request`, `native_response`, and
`response` in debug snapshots. Summary snapshots omit those bodies.

## Migration

| Existing path | New direct-call path |
| --- | --- |
| `Thinker.chat_ql` and the legacy parser accept ThinkerQL 0.3. | `model_call` and the new packet helpers accept 0.4. |
| Higher-level paths may validate outputs, qualify models, or orchestrate execution. | One native attempt with caller-owned acceptance and recovery. |
| Existing registry and provider machinery remain available. | Reuses registry route objects and credentials; no automatic routing. |
| Existing metrics continue to work. | Adds correlation and optional snapshots through the same metrics infrastructure. |

Move one caller at a time: construct the 0.4 packet, pin a supported route, call
`model_call`, then implement the application's status handling, transcript replay,
tool execution, validation, and bounded recovery around it. Do not send a 0.4
packet to `Thinker.chat_ql`, the legacy parser, or assume the legacy CLI chat
commands use this new function.

The new path does not issue qualification or assurance evidence. Applications
that depend on those gates must retain them explicitly when migrating. The old
code has not been removed; Dolphin and SIF callers are not automatically migrated
by installing this branch. A native Go library is available separately in
`thinker-go/`; see its manual for configuration and observability differences.
Automatic routing, a local-assistance loop, and an SSE decoder remain deferred.

## Troubleshooting and testing

| Symptom | Check |
| --- | --- |
| No trace files appear | This is the default. Enable debug or explicitly supply a recorder, then check its receipt. |
| Routine calls write a database | Metrics are separate from debug; use `metrics=False` or disable them in configuration. |
| Call is completed but JSON is invalid | Expected preservation behavior. The host validates or requests correction. |
| Schema worked before, but strictness changed | Explicitly set `json_schema_strict=True` for Chat Completions or Responses. |
| Strict flag fails before contacting Anthropic or Gemini | Omit the unmapped strict flag; the schema itself can still be forwarded. |
| No invocation is recorded | Preparation or credential lookup failed before transport; inspect the error and receipt ID. |
| Response contains an error with partial bytes | Decode only for inspection; do not treat partial wire JSON as a complete model answer. |
| Calls are unexpectedly slow after provider completion | Check observation duration, database contention, and storage-client timeouts. |
| Token or cost totals are unknown | The provider did not report a measurement; the new path does not invent one. |

Install development dependencies and run the focused offline tests from the root:

```sh
python -m pip install -e './thinker-core[dev]'
python -m pytest \
  thinker-core/thinker/tests/test_modelcalls.py \
  thinker-core/thinker/tests/test_thinkerql_packets.py \
  thinker-core/thinker/tests/test_thinkerql_errors.py \
  thinker-core/thinker/tests/test_call_observability.py
```

These tests use mocked model transports and storage clients. They cover packet
preservation, optional constraints, errors, cancellation, concurrent calls, and
observability. They do not establish live provider/model capability. For the full
suite, use `python -m pytest thinker-core/thinker/tests`; some legacy service tests
need permission to bind a local socket, and optional MLX smoke tests may be skipped.

See [the packet reference](THINKERQL_V0_4.md) for the full error taxonomy and
[the request schema](../thinker-core/thinker/thinkerql/request_v0_4.schema.json)
for the exact envelope definition.
