# Keeping Thinker's provider APIs current: lessons from ForgeCode (proposal, 2026-10-11)

Status: PROPOSAL. Nothing implemented.

Sources:
- ForgeCode `8703dec71` (v2.14.0-43), read at source level.
- Thinker `main` @ `f65c8e3`, plus the Go transport `thinker-go` @ `6bfdb40`, which is
  local-only (`refactor/preserve-model-output-v1` in `~/workspace/thinker-packet-v04`).

## 1. How ForgeCode stays current

| mechanism | evidence in Forge |
|---|---|
| **Provider catalog is data, separate from code** | `crates/forge_repo/src/provider/provider.json`: 48 providers. Each entry gives the URL, the wire protocol (`response_type`: OpenAI, OpenAIResponses, Anthropic, Google, Bedrock, OpenCode), auth, and models. Users can add or override entries in `.forge.toml` (`[[providers]]`). |
| **Model lists discovered at runtime** | 23 of 48 providers fetch their `/models` URL at runtime, cached for 7 days (`model_cache_ttl_secs`). New models appear without a release. The other 24 keep static lists that are pruned by hand: `c46ff7d67` removed 366 lines of retired models on 2026-10-01. |
| **API chosen by model family** | `chat.rs`: OpenAI models matching `gpt-5*` use the Responses API; everything else uses Chat Completions. Codex always uses Responses. Gemini always uses the native API (`streamGenerateContent`). |
| **Provider quirks as small, conditional request rewrites** | `crates/forge_app/src/dto/openai/transformers/`: 19 single-purpose rewrites, each applied only when its provider or model matches (`pipeline.rs`, `when_model.rs`). Examples: `make_openai_compat` (`max_tokens` → `max_completion_tokens`), `zai_reasoning` (GLM `thinking: enabled/disabled`), `strip_thought_signature`, `set_cache`, `set_reasoning_effort`, `normalize_tool_schema`. Anthropic and Google have their own rewrites (cache markers, thinking). A new quirk means a new rewrite plus a test; the core is untouched. |
| **Snapshot tests of the requests it builds** | `insta` snapshots for request conversion and for parsing model lists, per provider (12 under `provider/snapshots/`). These are offline. |
| **Speed of reaction** | Fixes land within days of a provider change: Opus 5.5 on 2026-09-28, the Sonnet 5.5 fix on 09-29, the Anthropic `refusal` stop reason, GPT-5.6 definitions. 101 commits since 2026-09-01; releases every 1–2 weeks; users auto-update daily. |
| **Dependencies** | Renovate with automerge. |

**What Forge does not have:**
- **No scheduled live provider tests.** CI runs on pull requests only.
- **No dates or sources on catalog entries.**
- **Model evals are manual.** `benchmarks/evals` runs real models by hand, and it tests tool-use behavior, not API conformance.

Forge stays current mostly through how fast its maintainers move and through user bug
reports. It does not detect drift; it reacts to it.

## 2. Where Thinker stands

- `spec/registry.yaml` pins each route's API style by hand. Some entries have dated
  comments; nothing re-checks them.
- `spec/model-turn-provider-conformance.yaml` rates capabilities from FAKE_TESTED to
  REAL_TESTED. Its real test results have **no dates**. The test for this file
  (`test_provider_conformance_manifest.py`) only checks that it is well-formed.
- Provider quirks are written inline in each adapter. For example,
  `openai_compatible.py:131` hardcodes `max_tokens`. When gpt-5.1 rejected it, the fix was a
  hand patch in Dolphin's vendored Go copy (`0.4+tlf1`, `token_limit_field`).
- **Two diverging lines.** Python `main` has the OpenAI family on the Responses API and Gemini
  on its native API. The Go transport, which Dolphin uses, exists only on this Mac.
- **No model discovery and no price dates** (`AUDIT-thinker.md` §price freshness).

## 3. What Thinker can adopt

Thinker must stay **reproducible** for frozen experiments, which Forge does not need. So:
copy Forge's structure, but never its silent runtime changes or auto-updating.

| # | proposal | taken from Forge | change for Thinker |
|---|---|---|---|
| A | **One source for the Go transport.** Push and merge `thinker-go`. Dolphin vendors tagged Thinker releases only, recorded in its `SOURCE.json`. Re-vendoring runs the conformance suite. | (single upstream) | Removes the single-machine risk. Fixes such as `tlf1` go upstream, not into vendored copies. |
| B | **Quirk-rewrite layer** in Python and Go: small conditional rewrites for the token-limit field, reasoning and thinking settings (OpenAI, Anthropic, Gemini, Z.ai/GLM through Together), cache markers and thought signatures. Each gets a captured-request test. | the `transformers/` pipeline | Each rewrite is listed by name in the call trace, so a frozen run shows exactly which ones applied. |
| C | **API policy by model family** in the registry. Defaults: `openai gpt-5*` → `responses_v1`, `gemini` → `generateContent_v1beta`, `anthropic` → `messages_v1` with cache markers. Routes can still override. | `chat.rs` routing | Applied when a route is created, and written into the route, so frozen catalogs never change underneath an experiment. |
| D | **Dated provenance in the conformance manifest.** Per family: docs URL, `checked_at`, API version or headers, and real tests with date, model and Thinker commit. The manifest test **warns** when `checked_at` is more than 30 days old. | (Forge has none) | Stricter than Forge; makes drift visible. |
| E | **Scheduled live smoke tests:** weekly, per family, three tests each (text, structured output, tool continuation). Costs cents. Results are appended to the manifest; a failure or deprecation header raises a flag. | (Forge has none) | Needs credentials on a runner (local launchd, or GitHub Actions secrets). That is a decision for the owner. |
| F | **Model discovery as dated snapshots.** `thinker models refresh` writes `spec/model-snapshots/<provider>-<date>.json` from each `/models` endpoint (OpenAI, Anthropic, Gemini, Together). The diff lists new and retired models. **It never edits routes.** | runtime `/models` discovery | Reproducible version of Forge's runtime lists. It would have found the Together GLM-5.x ids used on 2026-10-11. |
| G | **Tagged releases.** Dolphin moves to a new tag deliberately between experiment series. Frozen runs keep their pin. | Forge's release cadence | No auto-update. |

## 4. Suggested order

1. **A**: push the Go branch (needs owner approval) and tag a release.
2. **B**, starting with the two problems already seen: the token-limit field, and reasoning
   mapping for Anthropic and Gemini. Each with a captured-request test.
3. **D**: add dates and sources to the manifest, plus the staleness warning.
4. **C**: model-family API defaults. This lets Dolphin route gpt-5.x through Responses and
   Gemini through its native API (Gemini native also needs thought-signature replay in
   Dolphin).
5. **F**, then **E**: model snapshots, then scheduled live tests, once credentials for a
   runner are decided.

## 5. Not recommended

- Changing a frozen route's behavior at runtime: runtime model lists, auto-update, or
  rewrites added silently.
- Choosing API style from the model name alone without writing it into the route. Forge's
  `contains("gpt-5")` check is convenient, but it is invisible in a frozen config.
