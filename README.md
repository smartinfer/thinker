# Thinker

*An open-source project from **SmartInfer, Inc.** · Primary author: Anjan Goswami*

<!-- Branding note: the SmartInfer logo will be added here once an approved logo asset is available; none is bundled in this release. -->

Unified, minimal LLM access with a registry-driven router and ThinkerQL request language.

## Overview

Thinker provides a unified interface for accessing various LLM providers through a single request language (ThinkerQL) and a registry-driven routing system. The MVP supports OpenAI and local Ollama models with schema contracts, comprehensive testing, secure credential management, and real-time observability.

## Key Features

- **🔐 Secure Credential Management**: OS keyring integration and encrypted file storage
- **📊 Real-time Observability**: Live dashboard with usage statistics and performance metrics
- **🎯 Registry-driven Routing**: Intelligent model selection based on capabilities and requirements
- **📝 ThinkerQL Specification**: Unified request language for all LLM providers
- **💰 Cost Tracking**: Real-time cost monitoring and budget controls
- **🔄 Batch Processing**: Efficient processing of multiple requests
- **🛡️ Privacy-first Design**: No prompt content stored in metrics

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

### Secure Credential Management

```bash
source .thinker-env/bin/activate

# Set up API keys securely
thinker keys set openai
thinker keys set anthropic

# List configured providers
thinker keys list

# Test API keys
thinker keys test openai

# Rotate keys
thinker keys rotate openai
```

### Using the Registry CLI

```bash
source .thinker-env/bin/activate

# Validate registry
thinker registry-validate spec/registry.yaml

# Load registry
thinker registry-load spec/registry.yaml

# List available models
thinker registry-list
```

### Real-time Observability

```bash
source .thinker-env/bin/activate

# View usage statistics
thinker stats

# Live monitoring dashboard
thinker stats --live

# Filter by provider
thinker stats --provider openai

# Today's usage
thinker stats --today
```

### Running Tests

```bash
source .thinker-env/bin/activate
pytest thinker-core/thinker/tests/ -q
```

## CLI Commands

### Key Management
```bash
thinker keys set <provider>      # Set API key for provider
thinker keys list                # List configured providers
thinker keys test <provider>     # Test API key
thinker keys rotate <provider>   # Rotate API key
thinker keys delete <provider>   # Delete API key
```

### Observability
```bash
thinker stats                    # Show usage statistics
thinker stats --live            # Live updating dashboard
thinker stats --provider <name> # Filter by provider
thinker stats --today           # Today's usage
thinker stats --last-hour       # Last hour's usage
```

### Registry Management
```bash
thinker registry-validate <file> # Validate registry YAML
thinker registry-load <file>     # Load registry
thinker registry-list           # List available models
thinker registry-get <call_id>  # Get model details
```

### Chat Operations
```bash
thinker chat-ql --registry <file> --ql <file>
thinker map-chat-ql --registry <file> --ql-glob <pattern>
```

## Key Features

- **🔐 Secure Credential Management**: OS keyring integration and encrypted file storage
- **📊 Real-time Observability**: Live dashboard with usage statistics and performance metrics
- **🎯 Registry-driven Routing**: Intelligent model selection based on capabilities and requirements
- **📝 ThinkerQL Specification**: Unified request language for all LLM providers
- **💰 Cost Tracking**: Real-time cost monitoring and budget controls
- **🔄 Batch Processing**: Efficient processing of multiple requests
- **🛡️ Privacy-first Design**: No prompt content stored in metrics
- **🧪 Comprehensive Testing**: Unit, integration, and E2E test coverage

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

MIT License — Copyright (c) 2026 SmartInfer, Inc. See [LICENSE](thinker-core/LICENSE).

## Author & organization

Primary author: **Anjan Goswami**. Developed at **SmartInfer, Inc.**

## Other SmartInfer open-source projects

- [ProtoSpec](https://github.com/smartinfer/protospec) — deterministic software specification language.

(Thinker and ProtoSpec are architecturally independent; neither depends on the other.)
