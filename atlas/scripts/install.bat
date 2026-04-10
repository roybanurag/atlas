@echo off
setlocal enabledelayedexpansion

echo =======================================================
echo Atlas Installation Script for Windows
echo =======================================================
echo.

:: Check for Python
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.11+ from https://www.python.org/downloads/
    goto :end
)

:: Check for Ollama
ollama --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [WARNING] Ollama is not installed.
    echo You can install it from: https://ollama.ai/download/windows
    echo Atlas works best with Ollama for local LLM inference.
    echo.
    set /p CONTINUE="Continue installation without Ollama? (y/N) "
    if /i "!CONTINUE!" neq "y" goto :end
) else (
    echo [OK] Ollama is installed.
)

:: Install Atlas
echo.
echo Installing Atlas package in editable mode...
pip install -e .
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Installation failed.
    goto :end
)

echo.
echo =======================================================
echo Atlas installed successfully!
echo =======================================================
echo.
echo Next steps:
echo 1. Start Ollama:     ollama serve
echo 2. Pull a model:     ollama pull qwen3:14b
echo 3. Test Atlas:       atlas chat "Hello!"
echo.

:end
pause
