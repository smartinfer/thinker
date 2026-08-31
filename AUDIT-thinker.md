# Thinker implementation audit

Audit date: 2026-08-31. This was a read-only code audit except for this report. No credential command, provider request, setup script, package installation, or LLM network call was performed.

## 1. Verdict

Thinker is between a scaffold and a working MVP library. Roughly half of the README's user-facing promise is usable in code: ThinkerQL parsing, deterministic registry lookup, OpenAI chat calls, an Ollama-specific client, sequential batching, cost arithmetic, SQLite usage recording, a terminal dashboard, and two credential backends are real. The integrated product is substantially less complete: there is no installable `thinker` console entry point, no HTTP server, no per-registry-entry `base_url`, no MLX support, no Anthropic/Gemini runtime adapters, `registry-get` is deliberately nonfunctional, registry CLI state does not persist between invocations, and the Ollama ingestor routes discovered entries to the echo adapter. This is useful library code with tested-looking components, not yet the unified routing layer claimed by the README. A fair estimate is **about 50–60% of the broad README feature claims**, with important integration gaps.

## 2. Implemented vs. claimed

| Feature | README claim | Actual state | Evidence |
|---|---|---|---|
| ThinkerQL | Unified request language | **Implemented** for validation/parsing and chat orchestration; runtime only supports chat adapters | `thinker-core/thinker/thinkerql/parse.py:59`; `thinker-core/thinker/core.py:37` |
| Registry routing | Registry-driven routing | **Partial**: exact `call_id`, `provider/model`, or alias; first matching item wins. No policy/price ranking | `registry/resolver.py:37-64` |
| Registry validation/load | Validate and load registries | **Partial**: validation works; load is only into a fresh in-memory store and disappears at exit | `cli.py:62-71`; `registry/store.py:21-29` |
| Registry list | List available models | **Partial/non-useful**: each invocation creates an empty store, so it lists nothing | `cli.py:59-60,73-76` |
| Registry get | Get model details | **Absent**: always exits with “Run registry-load first”; separate invocations cannot share that store | `cli.py:78-80` |
| OpenAI adapter | OpenAI support | **Implemented for chat completions**, including images/JSON mode; not embeddings/tools execution; returns `unittest.mock.Mock` as production response | `adapters/openai.py:23-68` |
| Ollama adapter | Local Ollama support | **Implemented client**, with Ollama `/api/chat`, keep-alive/GPU hints and token extraction; integration is broken for ingested entries | `adapters/ollama.py:40-89`; `registry/ingestors/local_ollama.py:24-31` |
| Secure credentials | OS keyring plus encrypted fallback | **Implemented backends, partial integration**: both paths exist, but CLI defaults to keyring only rather than `auto` fallback | `registry/secure_credentials.py:23-34,51-80,98-113,197-299`; `cli.py:83` |
| Key test/rotation | Test and rotate provider keys | **Partial**: real tests only for OpenAI, Anthropic, Google; rotate does not restore the old key after a failed new-key test | `registry/auth.py:118-180`; `cli.py:128-153` |
| Cost tracking | Track model costs | **Implemented for calls made through `Thinker.chat_ql`** using response token counts and registry prices; no interception of other SDK/agent traffic | `core.py:85-108`; `pricebook.py:35-41` |
| Persistent observability | Real-time stats | **Implemented** with SQLite and Rich; recording errors are silently discarded | `observability/storage.py:17-98`; `observability/metrics.py:59-77`; `observability/dashboard.py:30-88` |
| Batch processing | Batch requests with budget | **Partial**: sequential loop, not parallel; budget is checked after the paid request has run | `primitives.py:13-55` |
| HTTP routing gateway | Agents/SDKs route through Thinker | **Absent** | No FastAPI/Flask/uvicorn/aiohttp or inbound route; the only `/v1/chat/completions` is an outbound OpenAI call at `adapters/openai.py:43` |
| Packaging/CLI | `thinker ...` commands | **Partial**: `main()` exists but `pyproject.toml` defines no `[project.scripts]` console command | `cli.py:19`; `pyproject.toml:1-35` |

### CLI surface

All commands are parsed by `main()` (`thinker-core/thinker/cli.py:19-228`), but the bare `thinker` executable is not declared in packaging.

| Command | Classification | Backing function/operation |
|---|---|---|
| `keys set` | **Implemented** (interactive; provider is not a positional argument despite README syntax) | `SecureCredentials.set()` — `secure_credentials.py:82-113` |
| `keys list` | **Implemented** for five hardcoded providers | `SecureCredentials.list_providers()` — `secure_credentials.py:142-172` |
| `keys test` | **Partial** | `SecureCredentials.test_key()` → `_test_provider_key()` — `secure_credentials.py:174-195`; `auth.py:118-180` |
| `keys rotate` | **Partial/unsafe semantics** | inline CLI branch; comment admits rollback is not implemented — `cli.py:128-153` |
| `keys delete` | **Implemented** | `SecureCredentials.delete()` — `secure_credentials.py:115-140` |
| `stats` and filters | **Implemented** over the metrics SQLite DB | `Dashboard.show_stats()` — `dashboard.py:30-47` |
| `registry-validate` | **Implemented** | `load_catalog()` — `loader.py:21-26` |
| `registry-load` | **Partial** (ephemeral) | `RegistryStore.apply_catalog()` — `store.py:27-29` |
| `registry-list` | **Partial/non-useful** (new empty store) | `RegistryStore.list_calls()` — `store.py:37-38` |
| `registry-get` | **Absent** | backing branch only raises `SystemExit` — `cli.py:78-80` |
| `chat-ql` | **Implemented** for registered chat adapters | `Thinker.chat_ql()` — `core.py:37-126` |
| `map-chat-ql` | **Partial** sequential batch | `map_chat()` — `primitives.py:13-55` |

Representative backing code:

```python
# cli.py:78-80
if args.cmd == "registry-get":
    raise SystemExit("Run `registry-load` first")

# resolver.py:46-63 (condensed)
if call_id: ... return c
if model: ... return candidates[0]
if alias: ... return candidates[0]
raise ValueError("ROUTING_INSUFFICIENT")
```

## 3. The three questions that matter

### Can a registry entry point at an arbitrary OpenAI-compatible `base_url` today?

**No.** `RegistryCall` has an optional `endpoint` field (`schema.py:38`), not a `base_url`, and neither `Thinker` nor `OpenaiAdapter` reads it. The global adapter registry constructs one `OpenaiAdapter()` at import time (`adapters/__init__.py:15-18`); that instance defaults to `https://api.openai.com` (`adapters/openai.py:19-21`) and posts to `self.base_url + /v1/chat/completions` (`:40-46`). Therefore adding `127.0.0.1:8080` to YAML alone will not route to `mlx_lm.server`. The legacy authenticated client honors process-wide `OPENAI_BASE` (`registry/auth.py:57-67`), but the runtime OpenAI adapter does not use that client or variable.

The Ollama adapter does something distinct: it uses Ollama's native `/api/chat` payload/response, flattens text parts, ignores images, supplies `num_ctx`, `num_gpu`, `keep_alive`, and a long timeout, and reads `prompt_eval_count`/`eval_count` (`adapters/ollama.py:25-89`). OpenAI uses `/v1/chat/completions`, bearer authentication, OpenAI multipart content, JSON response mode, and OpenAI `usage` fields (`adapters/openai.py:23-68`).

### Does an HTTP server exist?

**No. Thinker is library-and-CLI only.** There are no FastAPI, Flask, uvicorn, or aiohttp dependencies/imports and no inbound `/v1/chat/completions` or `/v1/messages` route. A server mode would reuse `Thinker.chat_ql`, registry resolver/store, adapters, price calculation, credentials, and metrics. Missing pieces are an ASGI server/application, OpenAI and possibly Anthropic request/response compatibility, streaming, lifecycle/config loading, stable response types, error/status mapping, authentication, concurrent request handling, and agent-facing model aliases.

### Is cost tracking real and persistent?

**Yes, within the narrow path of successful/failed calls made through `Thinker.chat_ql`.** Each request is inserted into SQLite with timestamp, provider, model, call ID, input/output tokens, latency, USD cost, status and error (`storage.py:30-98`). The default is `~/.thinker/metrics.db` (`config.py:34-37`). `thinker stats` queries and renders this real database; it is not placeholder output (`storage.py:100-249`; `dashboard.py:49-64`). It cannot observe calls made directly by Claude Code, Codex, Cursor, or another SDK because there is no proxy server.

The price mechanism is simply:

```python
# pricebook.py:35-41
input_cost = (input_tokens / 1000) * call.price.input_per_1k
output_cost = (output_tokens / 1000) * call.price.output_per_1k
return input_cost + output_cost
```

Despite requiring a “pricebook” file, `PriceBook.cost()` ignores its loaded `pricing_data` and uses prices embedded in the registry call (`pricebook.py:16-18,35-41`). Prices are hardcoded static values in YAML/ingestor metadata, with no effective date or refresh mechanism. The release notes date the release December 2024 (`RELEASE_NOTES_v0.2.0.md:3`); current accuracy cannot be established from this repository and should not be assumed.

## 4. Credential management and overlap with `~/Tools/devenv/key`

The secure implementation uses `keyring>=24` and `cryptography.Fernet` (`pyproject.toml:16-17`). Both paths exist. On macOS, Python `keyring` normally selects the macOS Keychain backend; Thinker stores generic-password items under service **`thinker-core`**, with provider names as usernames (`secure_credentials.py:31-34,51-55,98-102`). The exact Keychain database is selected by the user's macOS keyring configuration; Thinker does not choose a file path. Encrypted-file mode stores ciphertext at `~/.thinker/keystore.encrypted` and a mode-600 Fernet key at `~/.thinker/master.key`; the keystore is also chmod 600 (`secure_credentials.py:197-205,225-250`). Keeping the encryption key beside the ciphertext protects at rest mainly against disclosure of the keystore alone, not compromise of the account/filesystem.

The CLI calls `SecureCredentials()` with its default `storage_type="keyring"` (`cli.py:83`; `secure_credentials.py:23`), so its documented encrypted-file *fallback* is not active in normal CLI use. `ThinkerConfig` defaults to `auto`, but that setting is never passed into the CLI credential object (`config.py:22,44-50`). Environment fallback exists for `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and three other providers (`secure_credentials.py:67-79`; duplicated in `auth.py:26-39`). The OpenAI adapter itself checks `OPENAI_API_KEY` first, then secure storage, then legacy credentials (`adapters/openai.py:96-115`). No Anthropic runtime adapter exists.

`keys test` makes real GET requests to fixed public endpoints: OpenAI `https://api.openai.com/v1/models`, Anthropic `https://api.anthropic.com/v1/models`, and Google `https://generativelanguage.googleapis.com/v1beta/models`; Together and Mistral return “Unknown provider” despite being accepted by the CLI (`auth.py:129-173`). These tests ignore configurable provider base URLs.

The two key systems **coexist without direct conflict**: devenv uses separate mode-600 files under `~/.config/secrets/`; Thinker uses Keychain service `thinker-core`, `~/.thinker/*`, or environment variables. There is no code that reads devenv's directory or invokes its key tool, so Thinker cannot delegate to it today. Delegation could be added by having devenv export the standard environment variables before Thinker starts (already supported), or by adding a credential-provider adapter/explicit file integration. Copying keys into both systems creates duplication and rotation drift; environment handoff is the lowest-effort bridge.

## 5. Registry schema and actual routing

A model/call is defined by `call_id`, `provider`, `model_id`, `kind`, `modality`, capability strings (`caps`), token/rate `limits`, per-1K input/output `price`, adapter, payload style, optional endpoint, and aliases (`schema.py:17-50`). Resolution priority is exact call ID, then `provider/model` filtered by intent-derived kind, then alias; each path checks modality/capabilities and returns the first match. There is no cheapest/best/available selection and `AliasRule.order_by` is loaded but never used (`resolver.py:15-64`; `loader.py:28-34`; `schema.py:54-59`).

Full `spec/registry.yaml`:

```yaml
version: "1"
calls:
  - call_id: "openai:gpt-4o-mini.chat"
    provider: "openai"
    model_id: "gpt-4o-mini"
    kind: "chat"
    modality: "multimodal"
    caps: ["json_mode","tools","images_in"]
    limits: { max_input_tokens: 128000, max_output_tokens: 16384 }
    price: { input_per_1k: 0.00015, output_per_1k: 0.00060 }
    adapter: "openai"
    payload_style: "chat_completions_v1"
    aliases: ["openai:multimodal-cheap"]

  - call_id: "openai:text-embedding-3-large.embed"
    provider: "openai"
    model_id: "text-embedding-3-large"
    kind: "embed"
    modality: "embed"
    caps: []
    limits: { max_input_tokens: 8192, max_output_tokens: 0 }
    price: { input_per_1k: 0.00002, output_per_1k: 0.0 }
    adapter: "openai"
    payload_style: "embeddings_v1"

  - call_id: "local:echo.chat"
    provider: "local"
    model_id: "echo"
    kind: "chat"
    modality: "text"
    caps: ["json_mode"]
    limits: { max_input_tokens: 8192, max_output_tokens: 1024 }
    price: { input_per_1k: 0.0, output_per_1k: 0.0 }
    adapter: "local"
    payload_style: "echo"
```

## 6. Module inventory

Counts are physical lines from `wc -l`. “Real” means executable implementation or a deliberate package/registry interface; it does not mean complete or correctly integrated. There are **29 real/interface modules and 3 empty/abstract-only modules: 91% implemented by file surface**. Excluding empty `__init__` files, the only deliberate stub is the abstract `BaseAdapter.chat`; there are no `NotImplementedError`s. The `pass` statements in the resolver are successful no-op branches, while other `pass` uses swallow errors. This high file ratio overstates product completeness.

| Module | Lines | Purpose | State |
|---|---:|---|---|
| `thinker/__init__.py` | 15 | package export/version | Real interface |
| `cli.py` | 230 | argparse command dispatch | Real, partial integration |
| `config.py` | 78 | env/default paths | Real; constructor creates directories |
| `core.py` | 132 | parse, resolve, call, cost, record | Real |
| `migrate_secrets.py` | 119 | plaintext JSON migration | Real |
| `pricebook.py` | 41 | load file/calculate cost | Partial: loaded data unused |
| `primitives.py` | 55 | sequential map/budget | Real |
| `tokenization.py` | 77 | approximate word counting/truncation/schema check | Real; `tiktoken` imported but unused |
| `adapters/__init__.py` | 25 | singleton adapter registry | Real |
| `adapters/base.py` | 19 | abstract adapter contract | Abstract stub (`pass`) |
| `adapters/local_echo.py` | 64 | test echo backend | Real |
| `adapters/ollama.py` | 91 | Ollama native chat client | Real |
| `adapters/openai.py` | 116 | OpenAI chat-completions client | Real |
| `observability/__init__.py` | 14 | exports | Real interface |
| `observability/dashboard.py` | 194 | Rich static/live dashboard | Real (`pass` only handles Ctrl-C) |
| `observability/metrics.py` | 177 | collector/global recording API | Real; silently swallows recording failures |
| `observability/storage.py` | 309 | SQLite schema, inserts, aggregates, cleanup | Real |
| `registry/__init__.py` | 1 | empty package marker | Empty |
| `registry/auth.py` | 180 | env/file credentials, provider clients/key tests | Real, partial providers |
| `registry/loader.py` | 49 | YAML load/checksum/merge/alias rules | Real |
| `registry/resolver.py` | 64 | deterministic resolution/cap checks | Real; no policy selection |
| `registry/schema.py` | 59 | Pydantic registry models | Real |
| `registry/secure_credentials.py` | 314 | keyring/Fernet/env credentials | Real; broad exception swallowing |
| `registry/store.py` | 51 | in-memory registry | Real |
| `registry/ingestors/__init__.py` | 28 | duplicate static OpenAI seed ingestor | Real but oddly placed |
| `registry/ingestors/anthropic.py` | 61 | discover one known Anthropic model | Real ingestion; no matching adapter |
| `registry/ingestors/gemini.py` | 65 | discover one known Gemini model | Real ingestion; no matching adapter |
| `registry/ingestors/local_ollama.py` | 33 | discover Ollama tags | Partial/broken routing: emits adapter `local` (echo), ignores stored endpoint at runtime |
| `registry/ingestors/openai.py` | 75 | discover two known OpenAI models | Real ingestion |
| `registry/ingestors/openai_seed.py` | 36 | static OpenAI entries | Real, duplicates ingestors package file |
| `thinkerql/__init__.py` | 1 | empty package marker | Empty |
| `thinkerql/parse.py` | 129 | JSON-schema validation/normalization | Real |

## 7. Tests

Requested command and result:

```text
$ pytest thinker-core/thinker/tests/ -q --no-header 2>&1 | tail -30
zsh:1: command not found: pytest
```

Thus there is **no pass/fail count** in this environment, and dependencies were not installed. Static inventory finds **86 test functions** and no `skip`, `skipif`, or `xfail` markers, so there are no declared skipped tests or reasons to report. Tests cover schemas, resolver paths, loader merging, ThinkerQL parsing, core chat with mocks, echo E2E, sequential map, OpenAI adapter with mocked HTTP, authenticated clients, credential stores, provider ingestors with mocked HTTP, SQLite metrics, and CLI branches. There is no direct test of `OllamaAdapter`, dashboard rendering/live behavior, actual console-script installation, HTTP serving, `registry-get`, cross-process registry persistence, Anthropic/Gemini runtime chat, MLX, streaming, or coding-agent integration. No real provider call is required by the apparent suite.

## 8. Dependencies and Apple Silicon fit

Root `requirements.txt` lists Pydantic, PyYAML, httpx, Rich, tiktoken, and development tools, but omits runtime dependencies `jsonschema`, `keyring`, and `cryptography` that are present in `pyproject.toml` and imported by the code (`requirements.txt:5-16`; `pyproject.toml:9-18`). Installation from the root requirements alone is therefore incomplete. `pyproject.toml` requires Python `>=3.11` with no upper bound, so uv-managed 3.12 and 3.13 are declared compatible. Black/mypy configuration targets 3.11, not runtime enforcement (`pyproject.toml:28,35`).

Nothing in the declared dependency set is inherently incompatible with macOS arm64; `tiktoken` is enabled on all non-Windows platforms. Actual Python 3.13 wheel availability cannot be proven offline and was not tested. Neither `mlx` nor `mlx-lm` appears anywhere in the repository. There is also no server dependency. The unused unconditional `import tiktoken` means Thinker fails to import if that optional-in-practice package is absent (`tokenization.py:10`), even though counting uses `str.split()`.

## 9. Gaps to the stated goal

1. **Make endpoints registry-driven — small.** Add `base_url` or consistently use `endpoint`, instantiate/configure adapters per call, and define local-auth behavior. Build on `RegistryCall`, `Thinker.chat_ql`, and `OpenaiAdapter`.
2. **Add an OpenAI-compatible HTTP server — large.** Implement `/v1/chat/completions` (and likely `/v1/models`), streaming, errors, lifecycle/config, concurrency, and stable schemas. Build on `Thinker`, resolver, adapters, and metrics.
3. **Connect MLX — small after gap 1 if `mlx_lm.server` is already running and response-compatible; medium otherwise.** Add an MLX registry entry and compatibility tests; Thinker need not host MLX itself unless desired. Build on `OpenaiAdapter`.
4. **Fix adapter integration — small.** Make Ollama ingestion select `ollama`, honor entry endpoints, and either add Anthropic/Gemini adapters or stop advertising ingested calls that cannot execute. Build on adapters/ingestors.
5. **Persist/load registry configuration for server and CLI — medium.** `registry-load/list/get` need shared persistent state or direct file arguments. Build on `RegistryStore` and loader.
6. **Expose a real executable/configuration path — small.** Add `[project.scripts]`, coherent defaults, and a usable price/registry configuration. Build on `cli.main` and `Thinker.from_files`.
7. **Make agent compatibility explicit — medium to large.** Verify each agent can set a compatible base URL and model name; add Anthropic compatibility if required, plus tool calls and streaming. Build on the new server surface. Codex/Cursor/Claude Code requirements differ and are not represented here.
8. **Harden accounting — medium.** Use exact provider usage when present, define fallback tokenizers, support cached/reasoning token categories, version/effective-date prices, prevent silent metrics loss, and decide local electricity/amortization pricing. Build on `PriceBook`, registry price, and observability.
9. **Make credentials coherent — small.** Honor `ThinkerConfig.credentials_storage_type`, implement true rollback for rotation, configurable test endpoints, and optionally environment delegation from devenv. Build on `SecureCredentials` and `auth.py`.
10. **Run and extend tests in a prepared environment — medium.** First establish the current 86-test baseline, then add server, MLX-compatible endpoint, Ollama runtime, persistence, streaming, and agent integration tests.

## 10. Recommendation

**Run Thinker alongside a dedicated MLX/OpenAI-compatible server for now; do not put agents behind Thinker yet.** `mlx_lm.server` (or another MLX-serving component) should own local inference. Thinker can be extended into the routing/accounting proxy, and its resolver, ThinkerQL parser, SQLite metrics, credential code, and cost arithmetic are a reasonable starting point. But the honest current answer is that it does **not** support the plan end-to-end: a registry entry cannot redirect the runtime OpenAI adapter, there is no inbound server for agents to target, and therefore its otherwise-real accounting sees only calls made through its Python/CLI API. Extending it is reasonable if ThinkerQL and custom routing are strategic; otherwise, use a mature OpenAI-compatible proxy/router for agent traffic and keep Thinker experimental.

## 11. Open questions

- Which exact agents must work first, and can each be configured for an OpenAI-compatible base URL, or is Anthropic `/v1/messages` compatibility required?
- Should Thinker proxy an independently managed `mlx_lm.server`, or launch/manage MLX model processes itself?
- Is streaming and tool calling required for the first usable agent integration? For coding agents, likely yes.
- Should local-model “cost” be zero, or include electricity, hardware amortization, and time?
- Is registry configuration intended to persist in SQLite/files, or always load at server startup?
- Should `~/Tools/devenv/key` remain the source of truth via environment injection, or should Thinker Keychain become the source of truth?
- What price source and update policy should replace undated hardcoded registry values?

## Remediation — integration defects

Remediation date: 2026-08-31. Scope was repair and verification only; no HTTP server, MLX hosting, Anthropic adapter, or Gemini adapter was added.

### Baseline

The exact requested `pytest ...` command failed because `pytest` was not on the shell `PATH`. Running the same suite through the existing project venv collected **106 tests: 106 passed, 0 failed**. The tree was already dirty with the uncommitted Task 1 and Task 2 changes across README/docs, examples, requirements, registry, adapters, CLI/core, tests, and tokenization, plus this untracked audit report. Starting HEAD was `dfbe413`; only three commits existed.

### Verification and actions

| Item | Verified state | Current-code evidence checked before repair | Action taken | Commit SHA | Tests added/strengthened |
|---|---|---|---|---|---|
| 1. Production `Mock` responses | **still broken → fixed** | OpenAI was already real: `return OpenaiResponse(...)`; Ollama still had `result = Mock()` and echo had `response = Mock()` | Added shared `AdapterResponse`; OpenAI, Ollama, and echo now return it; removed all runtime `unittest.mock` imports | `1ed8630` | Assert real response type/non-mock for OpenAI, echo, and mocked Ollama call |
| 2. Ollama ingestor routing | **already fixed** | `adapter="ollama"`, `endpoint=f"{base_url}/api/chat"`; core used `get_adapter(call.adapter, call.endpoint)` | No production change beyond committing the already-present endpoint plumbing; strengthened mocked ingest → resolve → adapter → call coverage | `849cbfd` | Existing integration test now asserts `OllamaAdapter`, exact stored endpoint, mocked POST, and real response type |
| 3. Pricebook contradiction | **already fixed** | `PriceBook.cost()` used only `call.price`; no `pricing_data` or `from_file` remained; `--pricebook` was gone | Kept **registry prices authoritative** because price is required by `RegistryCall` and travels with the routed call; committed removal of the external pricebook path and updated affected docs/examples | `b6cfd77` (with call-site cleanup also in `849cbfd` and `6e7dc94`) | Existing cost and CLI tests cover the retained path |
| 4. Hard `tiktoken` import | **already fixed** | `tokenization.py` began with typing imports and used `str.split()`; no `tiktoken` reference remained | Committed the already-present import removal | `3623885` | Existing token/count and core tests |
| 5. Missing dependencies | **still broken → fixed** | `jsonschema`, `keyring`, and `cryptography` were already added, but unused `tiktoken` remained in both manifests | Treated `thinker-core/pyproject.toml` as source of truth and root `requirements.txt` as its install-oriented mirror; removed the now-unused `tiktoken` dependency from both | `40af7a7` | Full clean dependency-backed suite |
| 6. Console entry point | **already fixed** | `[project.scripts] thinker = "thinker.cli:main"`; `def main():` takes no arguments | Verified editable install in `.venv` and successful `thinker --help`; no further change | `40af7a7` | CLI suite plus installed command inspection |
| 7. Key rotation rollback | **still broken → fixed** | CLI executed `creds.set(provider, new_key)` before `creds.test_key(provider)` and only printed “Reverting” | `SecureCredentials.test_key` now accepts an unstored candidate; CLI validates candidate first and calls `set` only on success | `6e7dc94` | New failing-candidate CLI test asserts original stored value survives; no provider call |
| 8. Credential storage configuration | **still broken → fixed** | CLI used `SecureCredentials()` while config exposed `credentials_storage_type` and `keystore_path` | CLI now passes both configured values without changing the `auto` default | `6e7dc94` | New CLI constructor test asserts configured backend/path are honored |

Representative post-remediation code:

```python
# adapters/base.py
@dataclass
class AdapterResponse:
    text: str
    tokens: Dict[str, int]
    model: str
    provider: str

# registry/ingestors/local_ollama.py
adapter="ollama", payload_style="ollama_chat",
endpoint=f"{base_url}/api/chat"

# cli.py — candidate is tested before storage
success, message = creds.test_key(provider, new_key)
if success:
    if creds.set(provider, new_key):
        print(f"Key rotated successfully for {provider}")

# cli.py — configured credential backend
config = get_config()
creds = SecureCredentials(
    storage_type=config.credentials_storage_type,
    keystore_path=config.keystore_path,
)
```

### Dependency reconciliation

Third-party top-level runtime imports were enumerated directly from `thinker-core/thinker/`: `httpx`, `yaml` (PyYAML), `pydantic`, `rich`, `jsonschema`, `keyring`, and `cryptography`. Each appears in `pyproject.toml` and the root requirements mirror. Standard-library and intra-package imports were excluded. Test-only `pytest` and `respx` are present in the dev dependency sets. No runtime `tiktoken` import remains, so its dependency was removed.

### Final verification

Final result: **108 passed, 0 failed, 0 skipped**. This is an increase from the venv baseline of 106 because two credential CLI regression tests were added; existing adapter/Ollama tests were strengthened without inflating the count.

### Explicitly out of scope

**Registry persistence:** remains unresolved. The options are SQLite persistence, a state file, or requiring `--registry` on `registry-list`/`registry-get`. Recommendation: make list/get accept `--registry`, matching `chat-ql`. It is the smallest deterministic repair, avoids cache invalidation/state migration, and keeps the registry YAML as the single source of truth. SQLite is justified only if mutable runtime registry state later becomes a requirement.

**HTTP server:** no work performed. Thinker remains library-and-CLI based.

**Anthropic/Gemini runtime:** no work performed. Both ingestors can advertise registry calls whose adapter names have no runtime implementation, so those ingested entries still cannot execute. Until adapters exist, callers should not load those entries as runnable routes.

**Price freshness:** registry prices remain undated and trace to the December 2024 release. They were deliberately not updated in this repair. A future price-refresh mechanism should record source and effective dates so historical cost accounting remains reproducible.
