# Thinker Go Library User Manual

The native Go library exposes `thinker.ModelCall` with the ThinkerQL 0.4 packet
contract. It calls providers directly from the host process. It needs neither a
Python runtime nor a Thinker HTTP service. Dolphin can keep selecting the model
for each turn and use this library for the provider exchange.

This module adds the Go transport and its tests. Dolphin's existing transport has
not been migrated by this change.

## Installation

The module is in `thinker-go/`, uses Go 1.21 or newer, and has no external module
dependencies. For local development, add it to the calling application's module
with a local replacement pointing to your Thinker checkout:

```sh
go mod edit -require=thinker@v0.0.0
go mod edit -replace=thinker=/absolute/path/to/thinker/thinker-go
```

The module name is `thinker`. A remote module release path has not been published.
These commands edit the caller's `go.mod`; this implementation did not run them
in Dolphin. From a Go source file, use `import "thinker"`.

## Direct calls

```go
result := thinker.ModelCall(ctx, packet, route, thinker.Options{})
```

`ModelCall` blocks the calling goroutine until the model attempt and optional
observation work finish. It returns a packet for provider errors, malformed
requests, timeouts, and cancellation. It does not return a separate Go error for
those outcomes. Check `result["status"]` and `result["error"]`.

`thinker.Packet` is an alias for `map[string]any`. It can contain JSON-compatible
maps, slices, strings, booleans, numbers, and null. The packet is copied before
provider execution. Never mutate its maps or slices while a call is copying them.
Numbers decoded from native JSON use `json.Number` to preserve precision, including
large provider identifiers. Some host-generated fields, such as elapsed time, are
Go numeric values. Marshal the packet with `encoding/json` for storage or transfer.

`thinker.Route` contains the explicit `CallID`, `Provider`, `ModelID`, `Kind`,
`Adapter`, `PayloadStyle`, `Endpoint`, and `Aliases`. Its JSON names match the
corresponding Python registry fields. A Python registry entry can be decoded into
this type; additional catalog fields such as prices and limits are not used by
the Go direct-call function. There is no automatic registry lookup or routing.

The following complete program reads one selected route from a JSON file. Supply
that filename in `THINKER_ROUTE_FILE` and configure the provider key in its normal
environment variable before running it:

```go
package main

import (
    "context"
    "encoding/json"
    "fmt"
    "log"
    "os"
    "time"

    "thinker"
)

func main() {
    data, err := os.ReadFile(os.Getenv("THINKER_ROUTE_FILE"))
    if err != nil { log.Fatal(err) }
    var route thinker.Route
    if err := json.Unmarshal(data, &route); err != nil { log.Fatal(err) }

    packet := thinker.Packet{
        "version": "0.4",
        "request_id": "request-1",
        "job_id": "job-1",
        "turn_id": "turn-1",
        "intent": "chat",
        "messages": []any{
            thinker.Packet{
                "role": "user",
                "parts": []any{thinker.Packet{
                    "type": "text", "text": "Explain binary search.",
                }},
            },
        },
        "instructions": thinker.Packet{"max_output_tokens": 512},
        "timeout_ms": 30000,
    }
    ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
    defer cancel()
    result := thinker.ModelCall(ctx, packet, route, thinker.Options{})
    fmt.Println(result["status"])
    fmt.Println(result["content"])
    if failure, ok := result["error"].(map[string]any); ok {
        fmt.Println(failure["code"])
    }
}
```

Routine Go calls store no traces or metrics. The zero-value options are suitable
for direct execution without observation persistence.

## Options

| Field | Default and behavior |
| --- | --- |
| `Client` | Shared internal HTTP client with connection pooling, no environment proxy, and no redirects. An injected `*http.Client` remains owned by the caller. |
| `Credentials` | `EnvironmentCredentials`; optional `func(context.Context, string) (string, error)` receives the provider name. |
| `Debug` | `false`. Full redacted snapshots require an explicit opt-in. |
| `Recorder` | `nil`. Supplying a recorder enables summary snapshots; combine with debug for complete packet snapshots. |
| `Metrics` | `nil`. Optional `func(context.Context, Packet) error` receives detached, redacted scalar observations. |
| `MaxResponseBytes` | 16 MiB when zero or negative. Larger responses return `incomplete` with `PROTOCOL_ERROR` and the received prefix. |
| `ObservationTimeout` | Five seconds when zero or negative; separate from the model-call deadline. |

Credentials come from the same provider environment variable names used by
Python, including `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` or
`GOOGLE_API_KEY`, and `OPENROUTER_API_KEY`. Go does not read Python's OS keychain or
encrypted keystore. Inject a resolver if your application uses another source.
The `openai_compatible_local` adapter permits a missing key for a configured local
compatible endpoint.

Thinker does not retry the exchange or follow redirects. A supplied HTTP transport
must disable any retries it implements. The library applies its redirect policy
to a copy of the supplied client, leaving the original client configuration intact.
The host owns connection limits, proxy and TLS options, credentials, and shutdown
of its own clients.

## Optional constraints and native protocols

The caller chooses the model, vocabulary, schema, and whether to request a native
format restriction. No schema is added by default. For example:

```go
packet["instructions"] = thinker.Packet{
    "max_output_tokens": 512,
    "json_schema": thinker.Packet{
        "type": "object",
        "properties": thinker.Packet{
            "decision": thinker.Packet{
                "type": "string",
                "enum": []any{"accept", "revise", "ask"},
            },
        },
        "required": []any{"decision"},
        "additionalProperties": false,
    },
    "json_schema_strict": true,
}
```

`json_schema_strict` is optional and mapped only for Chat Completions and Responses.
Omit it for Anthropic and Gemini; their schema setting is still supported. Omission
leaves provider defaults in effect. Thinker forwards constraints, preserves
imperfect answers, and returns provider rejections without schema downgrade,
repair, or retries. The caller decides how to use or correct the result.

| Route payload style | Native protocol |
| --- | --- |
| `chat_completions_v1` | OpenAI-compatible Chat Completions |
| `responses_v1`, `openai_responses_v1` | Responses |
| `messages_v1` | Anthropic Messages |
| `generateContent_v1beta` | Gemini GenerateContent |
| `model_turn_v1` | Corresponding Responses, Anthropic, or Gemini protocol selected by adapter identity |

### Token limit and reasoning

`instructions.max_output_tokens` becomes `max_tokens` (Chat Completions and
Anthropic), `max_output_tokens` (Responses), or `maxOutputTokens` (Gemini). Models
that reject `max_tokens` on Chat Completions (OpenAI gpt-5.x) need the route field
`TokenLimitField: "max_completion_tokens"` (JSON `token_limit_field`); it is refused
on other protocols. Exactly one limit field is sent, and `provider_options` cannot
set a token limit.

`routing.reasoning` is either native or portable. A portable object has exactly one
key, `{"effort": E}` with E in `none`, `minimal`, `low`, `medium`, `high`, `xhigh`,
`max`, and is mapped per protocol:

| Protocol | Portable `{"effort": E}` becomes |
| --- | --- |
| Chat Completions | top-level `reasoning_effort: E` |
| Responses | `reasoning: {"effort": E}` |
| Anthropic | E = `none`: `thinking: {"type": "disabled"}`; other values are refused |
| Gemini | E = `none`: `thinkingConfig: {"thinkingBudget": 0}`; other values are refused |

Anthropic and Gemini budgets and levels are model-specific, so they are not guessed:
pass the provider's own object (for example `{"type": "enabled", "budget_tokens":
2048}` or `{"thinkingLevel": "low"}`), which is sent unchanged. Gemini 2.5 Pro and
3.x cannot disable thinking; the provider rejects `none` there. Sources and the dates
they were checked are in `spec/thinkerql-provider-apis.yaml`.

Multiple tools, raw tool arguments, tool results, native provider parts, Responses
continuation, and optional provider settings follow the
[packet reference](THINKERQL_V0_4.md). Multiple returned candidates remain in
`provider_data`; convenience fields describe the first. Thinker does not execute
tools or load a transcript from storage.

Supplied string arguments and results are preserved. When an argument or result
is a structured JSON object that the provider requires as a string, the library
serializes it; object key ordering may differ between Python and Go. That is not
a repair of model-generated text.

The function supports buffered chat responses. Automatic routing, SSE event
streaming, embeddings/OCR, native local-model invocation, and local-assistance
loops are not included. Provider options cannot overwrite transcript, identity,
tools, or continuation. Unsupported mappings return `UNSUPPORTED_REQUEST` before
contacting the provider.

## Goroutines and cancellation

The host can call `ModelCall` concurrently. No internal task scheduler, conversation
state, or per-job lock serializes independent calls. Shared clients and recorders
are allowed when their underlying implementations support concurrent use.

```go
results := make([]thinker.Packet, len(packets))
limit := make(chan struct{}, 4) // Illustrative host limit, not provider capacity.
var wg sync.WaitGroup
for i, packet := range packets {
    wg.Add(1)
    go func(i int, packet thinker.Packet) {
        defer wg.Done()
        select {
        case limit <- struct{}{}:
            defer func() { <-limit }()
        case <-ctx.Done():
            // ModelCall below also returns a cancellation packet without sending.
        }
        results[i] = thinker.ModelCall(ctx, packet, route, options)
    }(i, packet)
}
wg.Wait()
```

Import `sync` for this fragment. Each goroutine writes a separate result slot;
request/job/turn IDs identify outputs independently of completion order. Use fresh
request IDs per attempt. Do not mutate shared packets, route slices, options, or
callbacks while calls run. The host chooses rate limits and available concurrency.

The earlier of the caller context deadline and packet `timeout_ms` bounds the
provider exchange. Cancelling a context aborts the native HTTP request and returns
a `cancelled` packet. Deadline expiry returns `TIMEOUT`. Interrupted reads retain
exact received bytes, including a partial UTF-8 sequence, under
`provider_data.raw_body_base64`. These are wire bytes, not necessarily a complete
model answer.

Custom credential, metrics, and storage callbacks execute synchronously within
the calling goroutine and must honor the supplied context. The library does not
launch detached workers to hide uncooperative callbacks. Provider/transport
failures become packets; programmer panics in custom callbacks are not recovered.

## Errors and host recovery

Go uses the shared ThinkerQL error vocabulary, including all inspected Hermes
categories and network, timeout, conflict, rate limit, authentication, overload,
invalid request, context limit, cancellation, and broken-response errors.
`ClassifyError(ErrorInput{...})` is available for a host's own provider exchange.
Call it on a failure, not on generated assistant text.

`retryable` and `retry_after_seconds` are advisory. Dolphin or another host decides
whether to wait, refresh credentials, choose another model, repair a request, or
stop. Intelligence escalation and service failover remain distinct host decisions.
No output schema is enforced locally, and a `completed` call is not proof of
correctness or assurance admission.

The embedded schemas check packet envelopes. `ParseRequest`, `ParseResponse`, and
`PacketSchema` are available for explicit checks and interchange. The internal
validator supports the keywords used in those schemas; it is not an arbitrary
JSON Schema validation API. Generated strings and caller output schemas are never
evaluated as acceptance contracts. Non-JSON data, duplicate native JSON keys, and
envelope nesting deeper than 256 levels are rejected.

## Debug traces and metrics

```go
options := thinker.Options{
    Debug: true,
    Recorder: &thinker.TraceRecorder{
        Store: thinker.LocalTraceStore{Root: "./thinker-traces"},
    },
}
result := thinker.ModelCall(ctx, packet, route, options)
receipt := result["metadata"].(map[string]any)["observability"].(map[string]any)
```

With debug enabled and no recorder, the library uses `THINKER_TRACE_DIR` or
`~/.thinker/traces`. With a recorder and debug disabled, it writes only a summary.
Neither is active by default. Local recorder roots are literal filesystem paths;
use an expanded absolute home path instead of a `~` prefix.

Snapshots use `thinker.trace.v1`, matching the Python snapshot structure: IDs,
status, route, usage, cost, timing, and optional request/native request/native
response/result bodies. The trace represents a completed library call; the host
still owns overall job completion. Snapshots never become implicit model memory.

Known credentials, sensitive headers/fields, cookies, and common URL credentials
are redacted before storage. Debug snapshots intentionally retain application
content. `ExtraSecrets` and `Redactor` support additional host removal. The caller's
result is not redacted. Raw body hashes identify original bytes; stored base64
bodies are redacted. A custom HTTP transport may further modify a request after
Thinker captures its prepared snapshot.

Local snapshots are created atomically without overwriting existing files and use
private file permissions. Paths are grouped by hashed job and turn IDs with a
unique invocation ID. The returned trace receipt contains the exact URI and file
hash. See [the Python manual](USER_MANUAL.md#cloud-storage-and-retrieval) for the
shared grouping and retrieval conventions.

For S3 or GCS, implement `SnapshotStore.Write(ctx, key, data)` with the host's SDK
client or wrap it in `thinker.StoreFunc`. The callback receives redacted JSON and
must use create-only publication and respect the context. Go does not include
cloud SDK dependencies or ready-made S3/GCS adapters. This allows the application
to use its existing clients without a second credential configuration.

The optional `Metrics` callback receives only scalar IDs, route identity, outcome,
error code, elapsed time, reported token counts, and cost. Connect it to the host's
existing monitoring system. Go does not create a SQLite database, add another
event ledger, read Python's metrics environment switch, or install an exporter.
Missing usage/cost is null, not zero; no cost estimator is included.

Metrics and recording are awaited. Their receipts report `recorded`, `disabled`,
`failed`, or `interrupted` without replacing the model result. Persistence has a
separate context with a default five-second deadline so a cancelled model attempt
can still be recorded. Custom callbacks must honor that context. Observation
duration is reported separately; the model-call timeout does not include it.

## Python and Go differences

| Area | Python | Go |
| --- | --- | --- |
| Concurrency | Async calls, or separate event loops in worker threads | Caller-owned goroutines |
| Cancellation | Coroutine task cancellation | Context cancellation |
| Credentials | Environment plus existing keychain/keystore | Environment or injected resolver |
| Metrics default | Existing collector if enabled | Disabled until callback supplied |
| Cloud trace storage | S3/GCS wrappers accepting SDK clients | Host implements `SnapshotStore` using its SDK |
| Response size | Buffered without the Go cap | Default 16 MiB cap, configurable |
| Envelope nesting | Python parser limits | Explicit 256-level decode limit |
| Storage deadline | Caller/client storage settings | Separate observation context, default five seconds |

Both preserve imperfect generated content, forward optional constraints, make one
provider attempt, return the same packet/error vocabulary, and keep routing and
recovery with the host. These are compatible interfaces, not identical resource
policies. Set host budgets deliberately when comparing their performance.

## Dolphin integration boundary

Dolphin can keep its DTSP route selection, escalation, failover, transcript state,
tool sandbox, and completion gates. Its migration needs an adapter that constructs
a 0.4 packet and explicit `Route`, invokes `ModelCall`, then maps the returned
content, tool proposals, usage, and errors into Dolphin's existing turn handling.
Credentials move into the Go host process or its injected resolver because there
is no intermediary Thinker service.

This library does not itself replace Dolphin's existing `/v1/model-turn` adapter,
its old error-code projection, or its admission checks. That wiring remains a
separate migration. No routing module inside Thinker is required for it.

## Verification and contract maintenance

From `thinker-go/`:

```sh
go test -race ./...
go vet ./...
```

From the repository root, using the Python development environment:

```sh
python tools/sync_go_contract.py --check
python tools/check_go_parity.py
```

The provider API manifest test (`thinker/tests/test_provider_api_manifest.py`)
warns when `spec/thinkerql-provider-apis.yaml` has documentation or live evidence
older than 30 days.

The first command checks the embedded copies against the Python packet schemas
and error vocabulary. To refresh them after an intentional contract change, run
the same script without `--check` and review the resulting diff. The second
command builds offline cases from the Python implementation and compares Go
packet validation, wire translation, normalized responses, usage, and errors.
Python and pytest are required only for these development checks, never at Go
runtime.

Go tests cover four provider mappings, constraints, imperfect output, cancellation,
partial bytes, concurrency, redaction, storage failures, and create-only local
writes. Shared error fixtures are also read from the repository when present.
All provider exchanges use mock transports. These checks do not qualify a live
model or validate a deployed cloud-storage integration.
