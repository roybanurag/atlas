#!/bin/bash
# Atlas Installation Script

set -e

echo "🚀 Installing Atlas..."
echo ""

# Check for Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.11+ first."
    exit 1
fi

# Check Python version
PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
REQUIRED_VERSION="3.11"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo "❌ Python 3.11+ is required. You have Python $PYTHON_VERSION"
    exit 1
fi

echo "✓ Python $PYTHON_VERSION found"

# Check for Ollama
if ! command -v ollama &> /dev/null; then
    echo "⚠️  Ollama is not installed."
    echo "   Install from: https://ollama.ai"
    echo "   Or run: brew install ollama"
    echo ""
    read -p "Continue without Ollama? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo "✓ Ollama found"
fi

# Install Atlas
echo ""
echo "📦 Installing Atlas package..."
cd atlas
pip install -e .

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Atlas installed successfully!"
    echo ""
    echo "Next steps:"
    echo "1. Start Ollama: ollama serve"
    echo "2. Pull a model: ollama pull qwen3:14b"
    echo "3. Test Atlas: atlas chat 'Hello!'"
    echo ""
else
    echo "❌ Installation failed"
    exit 1
fi
