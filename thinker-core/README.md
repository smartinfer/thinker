# Thinker Core Library

Core library for Thinker - registry-driven LLM access with ThinkerQL specification.

## Installation

```bash
pip install -e .
```

## Usage

### Basic Usage

```python
from thinker import Thinker
from thinker.registry.store import RegistryStore
from thinker.pricebook import PriceBook
from thinker.registry.schema import RegistryCall, Limits, Price

# Create a registry store with models
store = RegistryStore()
call = RegistryCall(
    call_id="local:echo.chat",
    provider="local",
    model_id="echo",
    kind="chat",
    modality="text",
    caps=["json_mode"],
    limits=Limits(max_input_tokens=8192, max_output_tokens=1024),
    price=Price(input_per_1k=0.0, output_per_1k=0.0),
    adapter="local",
    payload_style="echo"
)
store.put_call(call)

# Create Thinker instance
pricebook = PriceBook()
thinker = Thinker(store, pricebook)

# Process a ThinkerQL request
ql = {
    "version": "0.3",
    "intent": "chat",
    "messages": [
        {
            "role": "user",
            "parts": [{"type": "text", "text": "Hello, world!"}]
        }
    ],
    "routing": {"call_id": "local:echo.chat"}
}

response = thinker.chat_ql(ql)
print(f"Response: {response.text}")
print(f"Model: {response.model}")
print(f"Provider: {response.provider}")
print(f"Tokens: {response.tokens}")
print(f"Cost: ${response.cost_usd:.4f}")
```

### CLI Usage

```bash
# Validate registry
python -m thinker.cli registry-validate spec/registry.yaml

# Process single ThinkerQL file
python -m thinker.cli chat-ql --registry spec/registry.yaml --pricebook spec/pricebook.yaml --ql examples/01_text_chat_ql.yaml

# Process multiple ThinkerQL files
python -m thinker.cli map-chat-ql --registry spec/registry.yaml --pricebook spec/pricebook.yaml --ql-glob "examples/*.yaml" --budget-usd 0.05
```

### Environment Variables

Set API keys for different providers:

```bash
export OPENAI_API_KEY="sk-your-key"
export ANTHROPIC_API_KEY="sk-ant-your-key"
export TOGETHER_API_KEY="your-key"
export MISTRAL_API_KEY="your-key"
export GOOGLE_API_KEY="your-key"
```

## Development

```bash
# Install development dependencies
pip install -e .[dev]

# Run tests
pytest -q

# Run tests with coverage
pytest --cov=thinker --cov-report=term-missing

# Format code
ruff format .
black .

# Type checking
mypy thinker/
```

## Features

- **Registry-driven routing**: Centralized model management and routing
- **ThinkerQL specification**: Unified request language for all LLM providers
- **Multiple providers**: Support for OpenAI, Anthropic, Google, Together, Mistral, and local models
- **Batch processing**: Process multiple requests with order preservation and budget controls
- **Schema validation**: JSON schema validation for requests and responses
- **Cost tracking**: Built-in cost calculation and budget management
- **CLI interface**: Command-line tools for registry management and request processing
- **Comprehensive testing**: 95%+ test coverage with unit, integration, and E2E tests

## Author

Anjan Goswami