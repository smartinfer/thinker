#!/bin/bash
# Setup script for Thinker Core development environment

echo "Setting up Thinker Core development environment..."

# Check if virtual environment exists
if [ ! -d ".thinker-env" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .thinker-env
fi

# Activate virtual environment
echo "Activating virtual environment..."
source .thinker-env/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# Install requirements
echo "Installing core dependencies..."
pip install -r requirements.txt

echo "Installing development dependencies..."
pip install -r requirements-dev.txt

# Install thinker-core in editable mode
echo "Installing thinker-core in editable mode..."
pip install -e thinker-core/

echo "Setup complete!"
echo "To activate the environment: source .thinker-env/bin/activate"
echo "To run tests: pytest thinker-core/thinker/tests/"
echo "To run CLI: python thinker-core/thinker/cli.py"
