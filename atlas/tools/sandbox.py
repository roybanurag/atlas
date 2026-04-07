"""Secure OS-level sandboxing for code and command execution.

Provides Docker-based isolated execution for Python scripts and Bash commands.
Builds a persistent local image with standard data science and web libraries
for fast, secure subsequent runs.
"""

import os
import subprocess
import tempfile
from textwrap import dedent

from langchain_core.tools import tool

IMAGE_NAME = "atlas-sandbox:latest"

# Standard payload of heavily-used packages for LLM analysis
DOCKERFILE_CONTENT = dedent("""\
    FROM python:3.11-slim
    
    # Install basic utilities
    RUN apt-get update && apt-get install -y curl wget jq git && rm -rf /var/lib/apt/lists/*
    
    # Pre-install heavy and common python libraries to avoid runtime pip installs
    RUN pip install --no-cache-dir \\
        requests \\
        beautifulsoup4 \\
        pandas \\
        numpy \\
        scipy \\
        urllib3 \\
        yfinance \\
        matplotlib
    
    WORKDIR /workspace
    # Run as a non-root user by default for safety
    RUN useradd -m sandboxuser && chown -R sandboxuser /workspace
    USER sandboxuser
""")

def _check_docker_daemon() -> None:
    """Verify Docker daemon is reachable. Raises RuntimeError with helpful message if not."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            raise RuntimeError("Docker daemon not responding")
    except (subprocess.TimeoutExpired, FileNotFoundError, RuntimeError) as exc:
        raise RuntimeError(
            "\n╔════════════════════════════════════════════════════════════╗\n"
            "║  Atlas Sandbox: Docker is not running                      ║\n"
            "║                                                            ║\n"
            "║  Secure code execution requires Docker Desktop.           ║\n"
            "║  To fix:                                                   ║\n"
            "║    1. Open Docker Desktop                                  ║\n"
            "║    2. Wait until it shows 'Running'                        ║\n"
            "║    3. Retry your request                                   ║\n"
            "╚════════════════════════════════════════════════════════════╝"
        ) from exc


def _ensure_image_exists() -> None:
    """Build the sandbox image if it doesn't exist."""
    _check_docker_daemon()

    # Check if image exists
    result = subprocess.run(["docker", "images", "-q", IMAGE_NAME], capture_output=True, text=True)
    if not result.stdout.strip():
        print(f"Building Docker image {IMAGE_NAME} for secure sandboxing (this will take a moment)...")
        with tempfile.TemporaryDirectory() as td:
            df_path = os.path.join(td, "Dockerfile")
            with open(df_path, "w") as f:
                f.write(DOCKERFILE_CONTENT)
            
            build_result = subprocess.run(
                ["docker", "build", "-t", IMAGE_NAME, "."],
                cwd=td,
                capture_output=True,
                text=True,
            )
            if build_result.returncode != 0:
                raise RuntimeError(
                    f"Docker image build failed:\n{build_result.stderr[-1000:]}"
                )


def execute_in_sandbox(command: list[str], input_str: str | None = None, timeout: int = 60) -> str:
    """Execute a command in the ephemeral sandbox container."""
    try:
        _ensure_image_exists()
    except Exception as e:
        return f"System Error: Failed to build or find sandbox Docker image: {e}"

    # We use --network none for total offline isolation, and drop capabilities
    docker_cmd = [
        "docker", "run", "--rm", "-i",
        "--network", "none",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        IMAGE_NAME
    ] + command

    try:
        process = subprocess.run(
            docker_cmd,
            input=input_str,
            text=True,
            capture_output=True,
            timeout=timeout
        )
        output = process.stdout
        if process.stderr:
            output += f"\n--- STDERR ---\n{process.stderr}"
            
        if process.returncode != 0:
            output += f"\n--- EXITED WITH CODE {process.returncode} ---"
            
        return output.strip() or "(No output)"
    except subprocess.TimeoutExpired:
        return f"Execution timed out after {timeout} seconds."
    except Exception as e:
        return f"Execution failed: {e}"

@tool("python_sandbox")
def python_sandbox(code: str) -> str:
    """Execute Python code in a secure OS-level sandbox container.
    
    You can use this tool to run analysis, make calculations, parse data, or execute logic securely.
    The container comes pre-installed with libraries: pandas, numpy, requests, beautifulsoup4, scipy, yfinance, matplotlib.
    Resulting stdout and stderr will be returned.
    
    Args:
        code: The Python script to execute.
    """
    return execute_in_sandbox(["python", "-c", code])

@tool("bash_sandbox")
def bash_sandbox(script: str) -> str:
    """Execute bash shell commands in a secure OS-level sandbox container.
    
    This command runs safely isolated from the host OS. Do not attempt to read host files with this.
    Basic utilities like curl, wget, jq, and git are available.
    
    Args:
        script: The bash script or command string to execute.
    """
    return execute_in_sandbox(["bash"], input_str=script)

def create_sandbox_tools() -> list:
    """Return the list of sandbox tools."""
    return [python_sandbox, bash_sandbox]
