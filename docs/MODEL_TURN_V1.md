# Thinker Model Turn V1

Protocol ID: `thinker.model-turn.v1`

This protocol is the provider-neutral model-inference boundary shared by future
Thinker clients. It carries messages, model-visible tool descriptions, tool
calls, tool results, structured output, transport continuation, usage, cost,
and normalized provider failures.

It is not an agent runtime. Thinker does not execute or authorize tools, edit
files, own worktrees, maintain agent memory, verify software, or make approval
decisions.

## Machine boundary

The local service binds loopback by default and exposes:

- `GET /health`: health, protocol, package version, and revision;
- `GET /v1/model-turn`: protocol discovery;
- `GET /v1/schemas/model-turn-request`: request JSON Schema;
- `GET /v1/schemas/model-turn-response`: response JSON Schema;
- `POST /v1/model-turn`: execute one turn;
- `DELETE /v1/model-turn/{request_id}`: request cancellation.

Start it with:

```text
thinker-model-turn-server --registry spec/registry.yaml --port 8787 \
  --revision <git-revision>
```

Provider credentials are resolved in the Thinker process. Request metadata
rejects credential-shaped fields, request bodies are not logged by the HTTP
handler, and normalized errors never include provider response bodies.

## Tools

`ToolDefinition` contains a name, description, strict JSON object input schema,
and strictness flag. A successful response can contain zero, one, or multiple
`ToolCall` values. The caller validates, authorizes, and executes them, then
sends `ToolResult` values identified by the original call IDs in a later turn.

An assistant message may retain earlier tool calls for ordinary message replay.
Thinker does not maintain a hidden conversation. A `Continuation.token` is an
opaque transport optimization; providers without such a mechanism can rely on
message replay.

## Structured output

When `response_schema` is present, a provider uses native strict schema output
when its registry capabilities include `json_schema` or `structured_output`.
Otherwise it uses the strongest JSON mode available. Thinker always validates
the decoded value locally. Invalid JSON or schema violations return
`StructuredOutputViolation`; malformed output is never returned as successful
structured data.

## Timeout and cancellation

`timeout_ms` is a per-request deadline and is forwarded to network providers.
On timeout, Thinker signals cancellation, returns `RequestTimeout`, and ignores
late provider output. `DELETE` signals an active request and causes the waiting
turn to return `Cancelled` promptly.

Cancellation is cooperative. Providers that cannot abort an in-flight call may
continue in a daemon worker, but the provider call is bounded by the same
per-request network timeout, and its eventual result is discarded. V1 therefore
guarantees bounded provider work, not instantaneous remote cancellation.

## Reproducibility and observability

Responses carry the requested route, resolved registry call, provider, model,
Thinker package version, and Thinker revision. Production launchers should pass
the exact Git revision through `--revision` or `THINKER_REVISION`; the fallback
is explicitly identified as a package version rather than a source revision.

The runtime observability callback receives only IDs, route/model provenance,
duration, usage, cost, finish reason, and normalized error. It receives no
messages, tool results, structured output, environment, or credentials.
