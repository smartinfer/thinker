# Thinker Core - Installation Guide

## Quick Start

### 1. Install from Source (Development)

```bash
# Clone or download the repository
cd thinker-core

# Install in development mode
pip install -e .

# Install with development dependencies
pip install -e .[dev]
```

### 2. Install from Wheel (Production)

```bash
# Build the package
python -m build

# Install from wheel
pip install dist/thinker_core-0.1.0-py3-none-any.whl
```

### 3. Verify Installation

```bash
# Test basic import
python -c "import thinker; print('✅ Version:', thinker.__version__)"

# Test CLI
python -m thinker.cli --help

# Run demo
python examples/demo.py
```

## Usage Examples

### Basic Python Import

```python
from thinker import Thinker
from thinker.registry.store import RegistryStore
from thinker.pricebook import PriceBook

# Create Thinker instance
store = RegistryStore()
pricebook = PriceBook()
thinker = Thinker(store, pricebook)

# Process ThinkerQL request
ql = {
    "version": "0.3",
    "intent": "chat",
    "messages": [{"role": "user", "parts": [{"type": "text", "text": "Hello!"}]}],
    "routing": {"call_id": "local:echo.chat"}
}

response = thinker.chat_ql(ql)
print(response.text)
```

### CLI Usage

```bash
# Validate registry
python -m thinker.cli registry-validate spec/registry.yaml

# Process single request
python -m thinker.cli chat-ql --registry spec/registry.yaml --pricebook spec/pricebook.yaml --ql examples/01_text_chat_ql.yaml

# Process batch requests
python -m thinker.cli map-chat-ql --registry spec/registry.yaml --pricebook spec/pricebook.yaml --ql-glob "examples/*.yaml"
```

## Environment Setup

### Required Environment Variables

```bash
# For OpenAI
export OPENAI_API_KEY="sk-your-key"

# For Anthropic
export ANTHROPIC_API_KEY="sk-ant-your-key"

# For Google
export GOOGLE_API_KEY="your-key"

# For Together
export TOGETHER_API_KEY="your-key"

# For Mistral
export MISTRAL_API_KEY="your-key"
```

## Package Structure

```
thinker-core/
├── thinker/                 # Main package
│   ├── __init__.py         # Package exports
│   ├── core.py             # Main Thinker class
│   ├── cli.py              # Command-line interface
│   ├── primitives.py       # Batch processing
│   ├── pricebook.py        # Cost management
│   ├── tokenization.py     # Token utilities
│   ├── adapters/           # Provider adapters
│   ├── registry/           # Registry system
│   ├── thinkerql/          # ThinkerQL specification
│   └── tests/              # Test suite
├── examples/               # Usage examples
├── spec/                   # Configuration files
├── pyproject.toml          # Package configuration
└── README.md              # Documentation
```

## Development

```bash
# Run tests
pytest -q

# Run with coverage
pytest --cov=thinker --cov-report=term-missing

# Format code
ruff format .
black .

# Type checking
mypy thinker/
```

## Troubleshooting

### Import Errors

If you get import errors, ensure:
1. Package is properly installed: `pip list | grep thinker`
2. Python path includes the package: `python -c "import sys; print(sys.path)"`
3. Virtual environment is activated (if using one)

### CLI Not Found

If CLI commands don't work:
1. Ensure package is installed: `pip install -e .`
2. Check Python path: `which python`
3. Try absolute path: `python -m thinker.cli --help`

### Missing Dependencies

If you get missing dependency errors:
1. Install all dependencies: `pip install -e .[dev]`
2. Check pyproject.toml for required packages
3. Update pip: `pip install --upgrade pip`
