# Apple Silicon / MLX local models

Thinker's direct MLX provider embeds the public `mlx-lm` Python API in the
Thinker process. It is another provider behind `thinker.model-turn.v1`; it does
not plan, execute tools, own agent memory, or decide whether a coding task is
complete.

## Install and inspect

Install the optional dependencies only on an Apple-Silicon Mac:

```bash
python -m pip install -e './thinker-core[mlx]'
thinker local doctor
```

Other Thinker providers remain importable when this extra is absent. An MLX
route then fails with an actionable `ModelUnavailable` response.

## Pull and register a model

Model weights stay in the standard Hugging Face cache. Thinker stores only a
portable alias containing the backend and Hugging Face repository ID:

```bash
thinker local pull mlx-community/Qwen3-8B-4bit
thinker local add state --backend mlx --model mlx-community/Qwen3-8B-4bit --tools
thinker local list
thinker local inspect state
```

Run the service with its normal registry. The standard local alias file is
loaded automatically; `--local-models` can select another file and
`--no-local-models` disables the overlay.

```bash
thinker-model-turn-server --registry spec/registry.yaml
thinker model-turn smoke mlx:state.chat --registry spec/registry.yaml
```

`local pull` uses Hugging Face's supported snapshot download operation and
does not load the model into unified memory. `local remove` is deliberately not
provided in V1 because Hugging Face snapshots may be shared by other programs;
removing one without explicit shared-cache ownership would be unsafe.

## Capabilities and structured output

Capabilities belong to the route/model entry, not to MLX in general. Text,
structured output, and tool use are gated independently. Thinker asks a local
model for JSON and then parses and validates it locally; this is
`THINKER_LOCAL_PARSE_AND_VALIDATE`, not model-native schema enforcement.
Malformed output fails closed as `StructuredOutputViolation`.

Native tool calls are accepted only when `mlx-lm` detects a tool-aware chat
template and parser. Thinker normalizes them to `ToolCall`, never executes the
tool, and accepts `ToolResult` through ordinary V1 message replay. Sophisticated
cross-request prompt/KV-cache reuse is deferred; the loaded model/tokenizer is
resident and reused within the Thinker process.

`MLX_PROMPT_CACHE: DEFERRED`

## MLX and Ollama are distinct

Ollama is an optional separate runtime/backend. The direct MLX provider loads
MLX/Safetensors repositories through `mlx-lm`. An Ollama-cached model must not
be assumed reusable by direct MLX. GGUF models remain usable through
Ollama/llama.cpp, even when an Ollama release also offers an MLX execution
engine. These remain distinct Thinker provider identities.

## Machine sizing

`mlx-community/Qwen3-8B-4bit` is the initial 16 GB validation model. It is an
example route, not a hardcoded runtime default. On a 128 GB Mac, pull a larger
MLX repository and change only the alias model ID:

```bash
thinker local add state --backend mlx --model ORGANIZATION/LARGER-MLX-MODEL
```

No provider or Model-Turn code change is required.
