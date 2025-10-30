# Story Generator (Thinker Example)

LLM-powered interactive agent that helps a parent create a bedtime story for a child under 6. 
Runs locally with Ollama (agent: qwen2.5:1.5b; story: phi4:14b) and can optionally use OpenAI.

## Prerequisites

- Python 3.11+
- Ollama running locally: `ollama serve`
- Models:
  - `ollama pull qwen2.5:1.5b`
  - `ollama pull qwen2.5:7b`
  - (optional) `ollama pull llama3.2:3b`
  - (optional) `ollama pull llama3.2:7b`
  - (optional) `ollama pull phi4:14b`
- (optional) OpenAI key: `python -m thinker.cli keys set`

## Install

```bash
cd ../../thinker-core
pip install -e .

# Optional: example-specific deps
pip install -r ../examples/story-generator/requirements.txt
```

## Run

```bash
cd ../examples/story-generator
python story_agent.py
```

Follow the prompts. The story will be saved under `outputs/`.

## Config

Edit `config.yaml` to change models or output options.


