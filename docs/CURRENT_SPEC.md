# Current Spec (Thinker Core / ThinkerQL v0.3)

## Payload & Validation
- ThinkerQL v0.3 with `intent` ∈ {chat, embed, ocr, doc_extract, docai_extract}.
- `messages` parts allowed: `text`, `image_url` (URL object), `document` (content, mime_type optional).
- Optional `documents` array; `instructions` map; `routing` (call_id/model/alias).
- Schema-validated; modality/caps inferred:
  - `embed` intent → modality embed.
  - Any `image_url` part → modality multimodal + cap `images_in`.
  - `json_schema` instruction → cap `json_mode`.

## Routing
- Explicit: `call_id`, `model`, or `alias`.
- Resolver enforces compatibility: intent→kind, required modality, required caps. No auto scoring or load balancing.

## Adapters
- OpenAI chat (supports image URLs), OpenAI embeddings.
- Ollama chat (text-only; ignores images).
- Local echo (testing).
- No streaming; no retries; adapter-level timeouts are fixed/env-based.

## Token & Cost
- Counts tokens; autoshrinks by truncating last user turn if over limits.
- Computes cost via pricebook; attaches `resp.cost_usd`, provider, model.
- Metrics recorded for success/failure with tokens, latency, cost.

## Batching
- `map_chat` to run multiple ThinkerQL requests sequentially with optional `budget_usd` guard; captures exceptions per item.

## Inputs Supported
- Text: full support across adapters.
- Images: URL-only; effective on OpenAI adapter.
- Documents: schema-allowed but not yet consumed by adapters (no OCR/doc extract implementation).
- No base64/file upload paths today.

