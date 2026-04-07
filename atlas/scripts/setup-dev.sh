#!/bin/bash
# Development Environment Setup Script

set -e

echo "🔧 Setting up Atlas development environment..."
echo ""

# Install in development mode
echo "📦 Installing Atlas with development dependencies..."
cd atlas
pip install -e ".[dev]"

if [ $? -ne 0 ]; then
    echo "❌ Installation failed"
    exit 1
fi

echo ""
echo "✅ Development environment ready!"
echo ""
echo "Available commands:"
echo "  pytest              - Run tests"
echo "  ruff check .        - Check code style"
echo "  ruff format .       - Format code"
echo "  atlas chat          - Test the agent"
echo ""
