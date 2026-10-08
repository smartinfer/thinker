# ThinkerQL 0.4 packet contract

This change defines packets for the stateless library interface. It adds envelope
parsing and provider-error classification, with offline fixtures. It does **not**
change model execution, implement routing, run tools, repair output, or add a
service. `Thinker.chat_ql` and `thinker.thinkerql.parse.parse_thinkerql` continue to
accept only the existing 0.3 contract. Do not send 0.4 packets to those functions.

The 0.4 JSON schemas ship with the Python package and can also be used by a future
native Go implementation. There is no Go runtime in this change.

## Public helpers

```python
from thinker.thinkerql import parse_request, parse_response, classify_error

request = parse_request({
    "version": "0.4",
    "request_id": "request-1",
    "job_id": "job-7",
    "turn_id": "turn-3",
    "intent": "chat",
    "messages": [{"role": "user", "parts": [{"type": "text", "text": "Answer in JSON"}]}],
    "routing": {"difficulty": "hard", "reasoning": {"effort": "high"}},
    "instructions": {"json_schema": {"type": "object"}},
})

# Imperfect model output remains valid packet content.
response = parse_response({
    "version": "0.4", "request_id": "request-1", "status": "completed",
    "content": '{"answer":7,}',
})

failure = parse_response({
    "version": "0.4", "request_id": "request-1", "status": "failed",
    "error": classify_error(http_status=429, headers={"Retry-After": "30"}),
})
```

These helpers perform no network calls and keep no request state. Each parser
returns an independent copy. They check the packet envelope and JSON
representability, never model text or tool arguments against an output schema.
Malformed envelopes raise `ValueError`; this is separate from a provider failure
returned in a valid response packet. `packet_schema("request" | "response")`
returns a fresh standalone JSON Schema.

## Requests and caller-owned state

- `request_id` identifies one library invocation. Optional `job_id` and `turn_id`
  correlate it with caller-owned work. No history is loaded using these IDs.
- `messages` carries the transcript. Assistant tool proposals retain their raw
  `arguments`; tool results carry `call_id`, `output`, and optional `is_error`.
  Top-level `tool_results` means new results appended after `messages`, in array
  order. A caller should use that field or tool messages for a result, not both.
- `tools` declares names and input schemas. Thinker does not execute them.
  `strict` and `instructions.json_schema` request provider constraints; they
  never authorize discarding generated content that fails those constraints.
- `continuation` and `provider_data` preserve opaque provider state, including
  signatures and encrypted reasoning, without interpreting it.
- `routing` permits one explicit `route_id`, `model`, or `alias`, or a difficulty
  hint (`simple`, `intermediate`, `hard`). `reasoning` holds provider-dependent
  settings. `local_mode` is `off`, `only`, or `assist`, with optional
  `local_route_id`. These fields describe intent; routing implementation follows
  in a later change. New `route_id` avoids confusing a registry route with an
  individual invocation's `call_id`.
- `timeout_ms`, `instructions`, and `provider_options` describe call settings.
  `metadata` holds application extensions. Unknown envelope fields are rejected;
  extension objects and model content remain open.
- Existing intent names (`chat`, `embed`, `ocr`, `doc_extract`, `docai_extract`)
  are retained as vocabulary, not a claim that execution supports them in 0.4.

## Responses and observations

`status` describes the call outcome: `completed`, `incomplete`, `refused`,
`failed`, or `cancelled`. Completion does not mean the application's task is
correct or accepted. A failed response requires an `error` object. A provider
refusal can preserve its text under `content` without fabricating a transport error.

`content` accepts any JSON value, including strings containing invalid JSON.
`tool_calls` may contain multiple proposals; their arguments are opaque, and
missing names/IDs can remain absent or null. The caller determines whether it
can execute a proposal or must ask the model for correction. Parsing a provider's
malformed wire envelope is a separate `PROTOCOL_ERROR`, not a content verdict.

For interrupted streams, retain partial content/tool arguments/continuation and
set `status: incomplete` with the error. Pass `stream_started=True` to the error
classifier once model response data has arrived; it marks `partial_response`
and distinguishes a disconnect during output from failure to connect. A timeout
still has code `TIMEOUT`; cancellation still has code `CANCELLED`. Streaming frame
decoding and converting async cancellation into packets belong to the future
call implementation; the classifier does not consume streams or catch exceptions.

`invocations` records local/cloud/direct subcalls with unique `call_id`, optional
`parent_call_id`, route, output, usage, cost, timing, and errors. It can preserve
the local draft alongside a later cloud answer. It is a result description, not
a second event ledger. Top-level usage/cost represent the whole library call;
do not add them to invocation-level values again. When any contributing usage
or cost is unknown, leave the corresponding total null or absent rather than
claiming zero. Preserve native usage fields in `usage.provider_data`.

`started_at` and `completed_at` are UTC RFC 3339 timestamps when available;
`duration_ms` is elapsed wall time. `resolved_route` records the actual provider,
model, and settings. `cost_usd` is optional; this change adds no pricing estimator.
No recorder, telemetry exporter, or S3/GCS storage is implemented here.

## Errors and host recovery

`classify_error` accepts an exception, HTTP status, provider error body, headers,
and provider name. It returns `code`, `message`, native `provider_code` and
`provider_type`, `http_status`, `retry_after_seconds`, `retryable`, and
`partial_response`. The caller may retain the complete native error envelope in
response `provider_data`. Recording/redaction is the recorder's responsibility.

Classification uses transport exceptions, explicit provider codes, narrow message
patterns, then HTTP status. It preserves numeric and HTTP-date Retry-After values
without an arbitrary cap. `retryable` is a transient-failure hint; the host decides
whether to retry, refresh credentials, change a request, or stop. The helper never
replays calls, switches providers, edits transcripts, or applies a retry budget.
Unknown codes remain available in `provider_code` with category `UNKNOWN` and no
automatic retry recommendation. The schema deliberately allows future error codes.

The taxonomy covers all 29 `FailoverReason` categories inspected in Hermes
`dde8800ed91c6e128064a17d5db914d74622594b` (`agent/error_classifier.py`):

| Hermes reason | Thinker code |
| --- | --- |
| auth / auth_permanent | AUTHENTICATION_FAILED / AUTHENTICATION_PERMANENT |
| billing | QUOTA_EXHAUSTED |
| rate_limit / upstream_rate_limit | RATE_LIMITED / UPSTREAM_RATE_LIMITED |
| upstream_blocked | UPSTREAM_BLOCKED |
| overloaded / server_error | PROVIDER_OVERLOADED / PROVIDER_ERROR |
| timeout / ssl_cert_verification | TIMEOUT / TLS_ERROR |
| context_overflow / payload_too_large | CONTEXT_LIMIT / PAYLOAD_TOO_LARGE |
| image_too_large / image_corrupt | IMAGE_TOO_LARGE / IMAGE_CORRUPT |
| model_not_found / model_entitlement | MODEL_UNAVAILABLE / MODEL_ENTITLEMENT |
| provider_policy_blocked / content_policy_blocked | PROVIDER_POLICY_BLOCKED / CONTENT_BLOCKED |
| incomplete_response / format_error | INCOMPLETE_RESPONSE / REQUEST_REJECTED |
| role_alternation | ROLE_ALTERNATION |
| invalid_encrypted_content | CONTINUATION_REJECTED |
| multimodal_tool_content_unsupported | MULTIMODAL_TOOL_CONTENT_UNSUPPORTED |
| reasoning_mandatory / thinking_signature | REASONING_REQUIRED / THINKING_SIGNATURE_INVALID |
| long_context_tier | LONG_CONTEXT_TIER |
| oauth_long_context_beta_forbidden | OAUTH_LONG_CONTEXT_BETA_FORBIDDEN |
| llama_cpp_grammar_pattern | GRAMMAR_UNSUPPORTED |
| unknown | UNKNOWN |

Additional codes are `CONNECTION_ERROR`, `CONFLICT`, `CANCELLED`, `BROKEN_STREAM`,
`PROTOCOL_ERROR`, `ACCESS_DENIED`, and `LOCAL_RESOURCE_EXHAUSTED`.
HTTP fallbacks include 408 timeout, 409 conflict, 429 rate limit, 401 authentication,
403 access denial, 400 invalid request, and all 5xx server errors (503/529 overload,
504 timeout). Native details refine these: a context-limit 400, exhausted-quota
429, expired-credential 403, and proxy-blocked 403 need different host handling.

This is category coverage with representative native error fixtures, not a copy
of every Hermes heuristic. Hermes recovery decisions that depend on agent state
remain the caller's responsibility. For example, only the host can establish
that auth failed *after* credential refresh or that repeated reasoning-only
responses exhausted its recovery policy. Such known categories can be carried
explicitly; the classifier does not infer them from one generated answer.

## Tests

`test_thinkerql_packets.py` covers malformed output, a host correction round trip,
partial responses, per-invocation usage, unknown cost, concurrent independence,
and the explicit 0.3/0.4 boundary. `test_thinkerql_errors.py` covers native error
fixtures, each Hermes category, transport exceptions, cancellation, and retry
metadata. Shared JSON fixtures are under `thinker/tests/fixtures/thinkerql`.

Run from the repository root:

```sh
python -m pytest thinker-core/thinker/tests/test_thinkerql_packets.py \
  thinker-core/thinker/tests/test_thinkerql_errors.py \
  thinker-core/thinker/tests/test_thinkerql_parse.py
```
