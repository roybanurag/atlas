#!/usr/bin/env python3
"""Test script to verify principles.md loading."""

import sys
from pathlib import Path

# Add the atlas package to the path
sys.path.insert(0, str(Path(__file__).parent / "atlas"))

from atlas.graph.nodes import load_principles


def main():
    """Test loading the principles file."""
    print("Testing principles.md loading...\n")
    
    principles = load_principles()
    
    if principles:
        print("✓ Successfully loaded principles.md")
        print(f"  Length: {len(principles)} characters")
        print(f"  Lines: {len(principles.splitlines())} lines")
        print("\nFirst 500 characters:")
        print("-" * 80)
        print(principles[:500])
        print("-" * 80)
        print("\nPrinciples loaded successfully!")
    else:
        print("✗ Failed to load principles.md")
        print("  Make sure principles.md exists in the project root")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
