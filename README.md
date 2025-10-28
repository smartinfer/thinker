# Thinker

Unified, minimal LLM access with a registry-driven router and ThinkerQL request language.

## Overview

Thinker provides a unified interface for accessing various LLM providers through a single request language (ThinkerQL) and a registry-driven routing system. The MVP supports OpenAI and local Ollama models with schema contracts and comprehensive testing.

## Project Structure

```
thinker/
├── .thinker-env/          # Virtual environment
├── spec/                  # Registry specifications
│   └── registry.yaml     # Model definitions
├── thinker-core/          # Core library
│   ├── thinker/          # Main package
│   ├── pyproject.toml   # Project configuration
│   └── README.md         # Core library docs
├── requirements.txt      # Core dependencies
├── requirements-dev.txt  # Development dependencies
└── setup.sh             # Setup script
```

## Quick Start

### Setup Development Environment

```bash
# Automated setup
./setup.sh

# Or manual setup
source .thinker-env/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
pip install -e thinker-core/
```

### Using the Registry CLI

```bash
source .thinker-env/bin/activate

# Validate registry
python thinker-core/thinker/cli.py registry validate spec/registry.yaml

# Load registry
python thinker-core/thinker/cli.py registry load spec/registry.yaml

# List available models
python thinker-core/thinker/cli.py registry list
```

### Running Tests

```bash
source .thinker-env/bin/activate
pytest thinker-core/thinker/tests/ -q
```

## Key Features

- **ThinkerQL**: Single request language for all LLM operations
- **Registry-driven routing**: Data-driven model/capability/price definitions
- **Provider adapters**: OpenAI and Ollama support
- **Schema contracts**: Deterministic token feasibility and cost bounds
- **Comprehensive testing**: Unit, integration, and E2E test coverage

## Architecture

```
ThinkerQL Request → Resolver → Registry → Adapter → Provider
```

- **ThinkerQL**: Request specification and validation
- **Resolver**: Routes requests based on registry rules
- **Registry**: YAML-based model/capability/price definitions
- **Adapter**: Provider-specific implementation (OpenAI, Ollama)

## Development

The project follows TDD (Test-Driven Development) with:
- Type hints mandatory
- Ruff + Black formatting
- MyPy strict type checking
- Google-style docstrings
- Comprehensive test coverage

## License

MIT License - see [LICENSE](thinker-core/LICENSE) file.

## Author

Anjan Goswami
