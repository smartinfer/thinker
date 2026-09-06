# Model-Turn V1 providers

`thinker.model-turn.v1` is the semantic protocol shared by callers. Provider
identity and transport implementation are deliberately separate registry
fields:

```text
requested route -> provider identity -> adapter/transport -> provider API
```

An OpenAI-compatible endpoint therefore uses `adapter: openai_compatible` but
retains its actual `provider` value (for example `deepseek`) in resolution and
response provenance. It is never reported as OpenAI merely because the wire
format is compatible.

## Capability gates

A registry call must advertise `model_turn_v1`. Requests additionally require
`tools`, `tool_result_continuation`, or `json_mode` when those semantics are
used. Resolution fails closed when a route lacks a requested capability.

The machine-readable conformance status is maintained in
`spec/model-turn-provider-conformance.yaml`. `MOCK_TESTED` means provider wire
payloads and responses were tested without quota. Only a successful call to an
actual provider may be marked `REAL_TESTED`.

## Adapter families

- `openai`: OpenAI Responses API, including native continuation IDs.
- `anthropic`: Anthropic Messages API. Tool continuation uses ordinary message
  replay; no provider continuation ID is exposed.
- `gemini`: Gemini native GenerateContent API. Provider tool-loop state is
  carried in the opaque V1 continuation token.
- `openai_compatible`: Chat Completions-compatible APIs. The registry owns the
  real provider name, endpoint, credential identity, model, and capabilities.
- `ollama`: Ollama native chat API. Capabilities remain model-specific and must
  not be inferred merely from the server being Ollama.
- `openai_compatible_local`: unauthenticated local compatible endpoints.

## Compatible-provider admission

The generic transport is not a blanket claim that every nominally compatible
provider implements every V1 capability. Remote compatible routes are enabled
only as explicit registry entries with model-specific capabilities.

- Protocol documentation currently supports future registry entries for
  DeepSeek, Together, Groq, Fireworks, and OpenRouter.
- No remote compatible route is enabled or marked real-tested in this revision.
- Zhipu/GLM, Moonshot, DashScope, Mistral, xAI, Cohere, and Hugging Face remain
  unvalidated for the complete V1 tool/continuation/structured-output contract.

This keeps `MODEL_KNOWN` separate from `MODEL_TURN_V1_CAPABLE`.

Thinker transports tool definitions, calls, and results. It never authorizes or
executes tools.

## Credentials

Credentials are resolved inside the Thinker process. Callers receive only safe
route, provider, model, usage, cost, duration, and error provenance. Official
coding-CLI subscription sessions are unrelated to these provider API adapters.
