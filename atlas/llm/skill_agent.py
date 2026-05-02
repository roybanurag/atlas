"""Skill Agent — AI-native codebase customization for Atlas.

Inspired by NanoClaw's "skills over features" philosophy: instead of manually
coding new integrations, describe what you want and the LLM generates,
reviews, and writes the tool module for you.

Usage (CLI):
    atlas skill "Add a currency conversion tool using the exchangerate.host API"
    atlas skill "Create a Hacker News top-stories tool"
"""

import asyncio
import re
import textwrap
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax

console = Console()

# System prompt that instructs the LLM to write an Atlas-compatible tool module
SKILL_SYSTEM_PROMPT = """\
You are an expert Python developer specialising in LangChain tool authoring for
the Atlas personal AI assistant framework.

Your task is to generate a single, self-contained Python module that implements
one or more LangChain tools based on the user's description.

Atlas tool conventions you MUST follow:
1. Each tool is a plain Python function decorated with @tool from langchain_core.tools.
2. The module must expose a create_<name>_tools() factory function that returns a list
   of tool instances.
3. Secrets (API keys) are retrieved via `from atlas.security import get_api_key`
   and the key name string should follow the pattern "<service>_api_key".
4. Never hard-code credentials.
5. Use standard library or pre-installed packages (requests, httpx) for HTTP.
6. Include docstrings for every tool using Google-style Args/Returns.
7. Handle exceptions gracefully and return a human-readable error string instead
   of raising.

Output format:
- Output ONLY the raw Python source code.
- Do NOT include markdown fences (```python) or any explanation text outside the code.
- The first line must be a module docstring describing the tool.
"""

REGISTRATION_PATCH_PROMPT = """\
Given the following tools_loader.py content, output ONLY the new version of the
_build_tool_registry return list (the lines between `return [` and the closing `]`)
with a new entry added for the provided factory function name and display name.
Do not output anything else — just the updated list lines.

Display name: {display_name}
Factory import: from atlas.tools.{module_name} import {factory_name}
"""


async def _generate_with_llm(prompt: str, system: str) -> str:
    """Run a single-shot generation with the configured local LLM."""
    from atlas.config.model_manager import ModelManager
    from langchain_core.messages import HumanMessage, SystemMessage

    mgr = ModelManager()
    llm = mgr.create_llm()  # uses default model from config

    messages = [SystemMessage(content=system), HumanMessage(content=prompt)]
    response = await llm.ainvoke(messages)
    return response.content.strip()


def _extract_code(raw: str) -> str:
    """Strip markdown fences if the model added them anyway."""
    # Remove ```python ... ``` or ``` ... ```
    fenced = re.search(r"```(?:python)?\n(.*?)```", raw, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return raw.strip()


def _derive_names(description: str) -> tuple[str, str, str]:
    """Derive module_name, factory_name, display_name from a free-form description.

    Returns:
        (module_name, factory_name, display_name)
    """
    # Pull the first noun phrase and sanitize to snake_case
    words = re.sub(r"[^a-zA-Z0-9 ]", "", description).lower().split()
    # Take the first 3 meaningful words as the name
    stop = {"a", "an", "the", "add", "create", "build", "make", "simple", "new"}
    key_words = [w for w in words if w not in stop][:3]
    base = "_".join(key_words) if key_words else "custom_tool"

    module_name = base
    factory_name = f"create_{base}_tools"
    display_name = base.replace("_", " ").title()
    return module_name, factory_name, display_name

# --- Safety: AST-based import restriction check ---

_BLOCKED_IMPORTS = {
    "subprocess", "shutil", "ctypes", "multiprocessing",
}

_BLOCKED_CALLS = {
    "os.system", "os.popen", "os.exec", "os.execv", "os.execve",
    "os.spawn", "os.spawnl", "os.spawnle",
    "eval", "exec", "compile", "__import__",
    "shutil.rmtree", "shutil.move",
}


def _validate_generated_code(code: str) -> tuple[bool, str]:
    """AST-based safety check on LLM-generated tool code.
    
    Returns:
        (is_safe, reason) — False with an explanation if dangerous constructs found.
    """
    import ast
    
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Generated code has a syntax error: {e}"
    
    for node in ast.walk(tree):
        # Check imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0]
                if mod in _BLOCKED_IMPORTS:
                    return False, f"Blocked import: '{alias.name}' (dangerous module)"
        
        if isinstance(node, ast.ImportFrom):
            if node.module:
                mod = node.module.split(".")[0]
                if mod in _BLOCKED_IMPORTS:
                    return False, f"Blocked import: 'from {node.module}' (dangerous module)"
        
        # Check dangerous function calls: eval(), exec(), os.system(), etc.
        if isinstance(node, ast.Call):
            func = node.func
            call_name = ""
            if isinstance(func, ast.Name):
                call_name = func.id
            elif isinstance(func, ast.Attribute):
                # e.g. os.system
                parts = []
                obj = func
                while isinstance(obj, ast.Attribute):
                    parts.append(obj.attr)
                    obj = obj.value
                if isinstance(obj, ast.Name):
                    parts.append(obj.id)
                call_name = ".".join(reversed(parts))
            
            if call_name in _BLOCKED_CALLS:
                return False, f"Blocked call: '{call_name}' (dangerous function)"
    
    return True, ""


async def run_skill_agent(
    description: str,
    tools_dir: Path,
    loader_path: Path,
    interactive: bool = True,
) -> None:
    """Main entrypoint: generate, preview, and optionally write a new skill.

    Args:
        description: Natural-language description of the desired tool.
        tools_dir: Path to atlas/tools/ directory.
        loader_path: Path to atlas/tools/tools_loader.py.
        interactive: If False (e.g. Slack), code is shown but NOT written.
    """
    console.print(f"\n[bold cyan]🛠  Skill Agent[/bold cyan] — generating: [italic]{description}[/italic]\n")

    module_name, factory_name, display_name = _derive_names(description)
    
    # Write community skills to a separate directory, not the main tools dir
    community_dir = tools_dir / "community"
    community_dir.mkdir(exist_ok=True)
    target_file = community_dir / f"{module_name}.py"

    # --- Step 1: Generate the tool module ---
    console.print("[dim]→ Asking local LLM to generate tool code...[/dim]")
    raw_code = await _generate_with_llm(
        prompt=f"Create an Atlas tool for: {description}\n\nFactory function name must be: {factory_name}",
        system=SKILL_SYSTEM_PROMPT,
    )
    code = _extract_code(raw_code)

    # --- Step 2: AST Safety Validation ---
    is_safe, reason = _validate_generated_code(code)
    if not is_safe:
        console.print(f"\n[red bold]⛔ Safety Check Failed[/red bold]: {reason}")
        console.print("[yellow]The generated code contains potentially dangerous constructs and will NOT be written.[/yellow]")
        console.print("[dim]You can manually review and save the code if you believe it is safe.[/dim]\n")
        # Still show the code for inspection
        console.print(Panel(
            Syntax(code, "python", theme="monokai", line_numbers=True),
            title=f"[bold red]BLOCKED: {target_file.name}[/bold red]",
            border_style="red",
        ))
        return

    # --- Step 3: Preview ---
    console.print()
    console.print(Panel(
        Syntax(code, "python", theme="monokai", line_numbers=True),
        title=f"[bold green]Generated: {target_file.name}[/bold green]",
        border_style="green",
    ))
    
    # --- Step 4: Interactive confirmation (disabled for Slack/non-interactive) ---
    if not interactive:
        console.print("[yellow]⚠ Non-interactive mode (Slack): code is displayed but NOT written to disk.[/yellow]")
        console.print("[dim]Use the CLI ('atlas skill') to write generated tools.[/dim]\n")
        return

    if target_file.exists():
        console.print(f"[yellow]⚠  {target_file} already exists.[/yellow]")
        if not Confirm.ask("Overwrite?", default=False):
            console.print("[dim]Aborted.[/dim]")
            return

    if not Confirm.ask("\n[bold]Write this file to atlas/tools/community/?[/bold]", default=True):
        console.print("[dim]Skill generation cancelled.[/dim]")
        return

    # --- Step 5: Write the tool module ---
    target_file.write_text(code)
    console.print(f"[green]✓ Written:[/green] {target_file}")

    # --- Step 6: Register in tools_loader.py ---
    console.print("\n[dim]→ Patching tools_loader.py...[/dim]")
    loader_src = loader_path.read_text()

    # Insert import before the return block
    import_line = f"    from atlas.tools.community.{module_name} import {factory_name}\n"
    if import_line.strip() not in loader_src:
        # Inject after existing imports inside _build_tool_registry
        loader_src = loader_src.replace(
            "    from atlas.tools.sandbox import create_sandbox_tools\n",
            f"    from atlas.tools.sandbox import create_sandbox_tools\n{import_line}",
        )

    # Insert registry entry before the closing bracket of the return list
    new_entry = f'        ("{display_name}", lambda: {factory_name}()),\n'
    if new_entry.strip() not in loader_src:
        loader_src = loader_src.replace(
            '        ("Briefing",      lambda: create_briefing_tool(data_dir)),',
            f'        ("Briefing",      lambda: create_briefing_tool(data_dir)),\n{new_entry.rstrip(",")},',
        )

    loader_path.write_text(loader_src)
    console.print(f"[green]✓ Registered[/green] '{display_name}' in tools_loader.py")

    console.print(f"\n[bold green]✅ Skill '{display_name}' created successfully![/bold green]")
    console.print(f"[dim]Restart Atlas to activate the new tool.[/dim]\n")
