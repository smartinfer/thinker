# Future Plan & Open Questions

## Prioritized roadmap

1) ROI/efficiency (same queries, cheaper/more reliable)
- Per-call retry/backoff and timeouts in ThinkerQL instructions; map provider errors to retryable codes.
- Request/response caching (pluggable store like Redis/file), hash of messages+model+instructions.
- Rate limits/QPS/concurrency controls; session/tenant budget caps; cheaper-model fallback on timeout/error.
- Smarter autoshrink (summarize vs truncate) and input dedup for repeated docs.

2) Multimodal expansion (images + generation)
- Accept image bytes/base64/local paths, not just URLs.
- Adapters: OpenAI vision, Ollama vision (when available), image generation (DALL·E/Stability) with caps `images_out`/`image_gen`.
- Schema: richer `image_url` detail and explicit image-gen outputs.

3) Documents (start with PDF, then Office)
- Accept doc uploads (bytes/base64/path) with mime types; size limits and page/range selection.
- Doc preprocessing (PDF → text; docx/pptx/xlsx → text) and routing to doc-capable models/tools.
- Wire `ocr`/`doc_extract`/`docai_extract` intents to actual adapters/pipelines.

4) Automatic semantic routing
- Policy layer to pick cheapest model that satisfies caps/modality/intent; optional quality tiers.
- Lightweight classifier/heuristics for small vs premium selection; A/B hooks; telemetry on decisions.

5) Stateful vs stateless usage (agent integration)
- Conversation/session handles so callers can reference prior turns without resending history.
- State store interface (memory/Redis/DB) and helpers to roll/prune history; streaming support.
- Agent helpers to persist/retrieve per-node state, share budgets/metrics across a run.

## Open questions to decide
- State: where to store (memory, Redis, DB)? Should Thinker manage session IDs or rely on caller-passed history?
- Inputs: prefer base64 in payloads or signed URLs/local paths for images/docs?
- Streaming: do we need streamed responses/tool events?
- Routing policy: acceptable heuristics for cheap vs premium? Any SLA or budget targets to optimize against?
- Caching: eviction policy and freshness guarantees; per-tenant scoping?
- Doc handling: which formats first after PDF, and acceptable size limits?

