"""Secrets management CLI commands."""

from typing import Optional

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt

console = Console()


def secrets_command(
    action: str = typer.Argument(..., help="Action to perform: set (store key), get (retrieve key), delete (remove key), list (show all services), or renew (renew OAuth credentials)"),
    service: Optional[str] = typer.Argument(None, help="Name of the service to manage (e.g., tavily, openai, anthropic, gmail)"),
):
    """Manage secure API keys and secrets.
    
    This command allows you to securely store, retrieve, and manage API keys for external services with your system's keyring.
    
    Actions:
    * set: Store a new API key (prompts for input securely)
    * get: Retrieve and display a masked version of the stored key
    * delete: Remove a stored key
    * list: Show all supported services and their configuration status
    * renew: Renew OAuth2 credentials for explicit Google services (e.g., gmail, calendar)
    
    Examples:
        atlas secrets set tavily
        atlas secrets get tavily
        atlas secrets list
        atlas secrets delete tavily
        atlas secrets renew gmail
    """
    from atlas.security import API_KEY_CONFIGS, SecretManager
    
    secret_manager = SecretManager()
    
    if action == "list":
        console.print("\n[bold]Available Services[/bold]\n")
        for svc_name, config in API_KEY_CONFIGS.items():
            # Check if key exists
            key = secret_manager.get_secret(config["keyring_name"], config["env_var"])
            status = "[green]✓ Set[/green]" if key else "[dim]○ Not set[/dim]"
            console.print(f"  {status} [cyan]{svc_name}[/cyan] - {config['description']}")
            console.print(f"      Get key at: {config['url']}")
        console.print()
        
    elif action == "set":
        if not service:
            console.print("[red]Error:[/red] Service name required")
            console.print("Usage: atlas secrets set <service>")
            return
        
        config = API_KEY_CONFIGS.get(service)
        if not config:
            console.print(f"[red]Error:[/red] Unknown service '{service}'")
            console.print("Run 'atlas secrets list' to see available services")
            return
        
        console.print(f"\n[bold]Setting API key for {service}[/bold]")
        console.print(f"Get your key at: [cyan]{config['url']}[/cyan]\n")
        
        if service == "google_oauth":
            import click
            import json
            from atlas.config.paths import get_config_dir
            
            console.print("This service requires a Google OAuth credentials.json file.")
            console.print("An editor will now open. Please paste the FULL contents of your downloaded credentials.json file.")
            console.print("Save and close the editor when done.")
            click.pause()
            
            pasted_text = click.edit(text="")
            if pasted_text is None or not pasted_text.strip():
                console.print("[red]✗[/red] No contents provided. Aborting.")
                return
                
            try:
                # verify it is valid JSON
                json.loads(pasted_text)
            except json.JSONDecodeError:
                console.print("[red]✗[/red] Invalid JSON provided. Please provide valid credentials.json content.")
                return
                
            # Write it to the config path
            cred_path = get_config_dir() / "google_oauth.json"
            cred_path.write_text(pasted_text)
            
            # The value we store inside keyring is just the path
            api_key = str(cred_path)
        else:
            api_key = Prompt.ask("Enter API key", password=True)
        
        if secret_manager.set_secret(config["keyring_name"], api_key):
            console.print(f"[green]✓[/green] Credentials for {service} stored securely in system keyring")
        else:
            console.print(f"[red]✗[/red] Failed to store API key")
            console.print(f"Fallback: export {config['env_var']}='your-key'")
        console.print()
        
    elif action == "get":
        if not service:
            console.print("[red]Error:[/red] Service name required")
            console.print("Usage: atlas secrets get <service>")
            return
        
        config = API_KEY_CONFIGS.get(service)
        if not config:
            console.print(f"[red]Error:[/red] Unknown service '{service}'")
            return
        
        key = secret_manager.get_secret(config["keyring_name"], config["env_var"])
        if key:
            # Show only last few characters for security
            masked = f"***{key[-4:]}" if len(key) >= 4 else "***"
            console.print(f"\n[green]✓[/green] API key for {service}: {masked}")
            source = "keyring" if secret_manager.get_secret(config["keyring_name"]) else "environment"
            console.print(f"    Source: {source}\n")
        else:
            console.print(f"\n[yellow]⚠[/yellow] No API key found for {service}")
            console.print(f"    Set it with: atlas secrets set {service}\n")
        
    elif action == "delete":
        if not service:
            console.print("[red]Error:[/red] Service name required")
            console.print("Usage: atlas secrets delete <service>")
            return
        
        config = API_KEY_CONFIGS.get(service)
        if not config:
            console.print(f"[red]Error:[/red] Unknown service '{service}'")
            return
        
        if Confirm.ask(f"Delete API key for {service}?", default=False):
            if secret_manager.delete_secret(config["keyring_name"]):
                console.print(f"[green]✓[/green] API key for {service} deleted")
            else:
                console.print(f"[yellow]⚠[/yellow] No API key found in keyring")
        console.print()
        
    elif action == "renew":
        if not service:
            console.print("[red]Error:[/red] Service name required")
            console.print("Usage: atlas secrets renew <service>")
            return
            
        from atlas.tools.google_auth import (
            GMAIL_AUTH, CALENDAR_AUTH, DRIVE_AUTH, TASKS_AUTH
        )
        auth_instances = {
            "gmail": (GMAIL_AUTH, "gmail", "v1"),
            "calendar": (CALENDAR_AUTH, "calendar", "v3"),
            "google_drive": (DRIVE_AUTH, "drive", "v3"),
            "google_tasks": (TASKS_AUTH, "tasks", "v1"),
        }
        
        if service not in auth_instances:
            console.print(f"[red]Error:[/red] Service '{service}' does not support explicit OAuth credential renewal.")
            return
            
        auth, api_name, api_version = auth_instances[service]
        
        if auth.token_file.exists():
            auth.token_file.unlink()
            console.print(f"[dim]Cleared existing {service} token cache.[/dim]")
            
        console.print(f"\n[bold]Renewing credentials for {auth.service_name}[/bold]")
        console.print("This will open a browser window to authenticate with Google.")
        
        try:
            auth.get_service(api_name, api_version)
            console.print(f"[green]✓ Authentication successful![/green]\n")
        except Exception as e:
            console.print(f"[red]✗ Failed to renew credentials:[/red] {e}\n")
        
    else:
        console.print(f"[red]Error:[/red] Unknown action '{action}'")
        console.print("Valid actions: set, get, delete, list, renew")
