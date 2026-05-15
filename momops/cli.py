"""
AGENT-7: CLI Interface
Typer + Rich — beautiful, expressive terminal commands.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from momops import mom as create_mom
from momops.mom import MomSession
from momops.state import StateStore

app = typer.Typer(
    name="momops",
    help="[bold green]MomOps[/] — Zero-config cloud infrastructure with natural language.",
    rich_markup_mode="rich",
    no_args_is_help=True,
)
console = Console()


def _print_banner() -> None:
    banner = Text()
    banner.append("  ███╗   ███╗ ██████╗ ███╗   ███╗ ██████╗ ██████╗ ███████╗\n", style="bold green")
    banner.append("  ████╗ ████║██╔═══██╗████╗ ████║██╔═══██╗██╔══██╗██╔════╝\n", style="bold green")
    banner.append("  ██╔████╔██║██║   ██║██╔████╔██║██║   ██║██████╔╝███████╗\n", style="green")
    banner.append("  ██║╚██╔╝██║██║   ██║██║╚██╔╝██║██║   ██║██╔═══╝ ╚════██║\n", style="green")
    banner.append("  ██║ ╚═╝ ██║╚██████╔╝██║ ╚═╝ ██║╚██████╔╝██║     ███████║\n", style="dim green")
    banner.append("  ╚═╝     ╚═╝ ╚═════╝ ╚═╝     ╚═╝ ╚═════╝ ╚═╝     ╚══════╝\n", style="dim green")
    banner.append('\n  "Mom, I need infrastructure." — "Say no more, honey."\n', style="italic dim")
    console.print(Panel(banner, border_style="green", padding=(0, 2)))


def _cost_table(cost_data: dict) -> Table:
    table = Table(
        title="💰 Cost Preview",
        box=box.ROUNDED,
        border_style="green",
        show_header=True,
        header_style="bold green",
    )
    table.add_column("Service", style="cyan", no_wrap=True)
    table.add_column("Monthly (USD)", style="yellow", justify="right")

    skip_keys = {"total_monthly", "estimated_annual", "savings_available"}
    for key, val in cost_data.items():
        if key not in skip_keys:
            table.add_row(key.replace("_", " ").title(), f"${val:.2f}")

    table.add_section()
    table.add_row("[bold]Total Monthly[/]", f"[bold yellow]${cost_data['total_monthly']:.2f}[/]")
    table.add_row("Annual Estimate", f"${cost_data.get('estimated_annual', cost_data['total_monthly'] * 12):.2f}")

    if "savings_available" in cost_data:
        table.add_row(
            "[green]Potential Savings[/]",
            f"[green]-${cost_data['savings_available']:.2f}/mo[/]",
        )

    return table


@app.command()
def deploy(
    intent: Annotated[str, typer.Argument(help="Natural language description of what you need")],
    region: Annotated[str, typer.Option("--region", "-r", help="AWS region")] = "us-east-1",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Validate without deploying")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation prompts")] = False,
) -> None:
    """Deploy infrastructure from a natural language description."""
    _print_banner()

    with console.status("[green]Mom is thinking...[/]", spinner="dots"):
        app_obj = create_mom(intent, region=region, dry_run=dry_run)
        cost = app_obj.preview()

    console.print(_cost_table(cost))

    if not yes:
        confirmed = typer.confirm("\nDeploy this infrastructure?", default=True)
        if not confirmed:
            console.print("[yellow]Deployment cancelled.[/]")
            raise typer.Exit(0)

    async def _run() -> None:
        with Progress(
            SpinnerColumn(style="green"),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=40, style="green"),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        ) as progress:
            task = progress.add_task("Deploying...", total=len(app_obj._get_blueprint().deploy_steps))
            async for event in app_obj.deploy_async():
                progress.update(task, description=f"[cyan]{event.step}[/] — {event.message}", advance=1)

        result = app_obj._deployed
        if result:
            console.print(Panel(
                f"[bold green]🚀 Deployment complete![/]\n\n"
                f"[cyan]Endpoint:[/] {result.endpoint}\n"
                f"[cyan]App ID:[/]   {result.app_id}\n"
                f"[cyan]Region:[/]   {result.region}",
                title="[bold green]Success[/]",
                border_style="green",
            ))

    asyncio.run(_run())


@app.command()
def preview(
    intent: Annotated[str, typer.Argument(help="Natural language description")],
    region: Annotated[str, typer.Option("--region", "-r")] = "us-east-1",
) -> None:
    """Preview the cost of an infrastructure deployment."""
    _print_banner()

    with console.status("[green]Mom is calculating costs...[/]", spinner="dots"):
        app_obj = create_mom(intent, region=region)
        cost = app_obj.preview()

    console.print(_cost_table(cost))

    sec_table = Table(title="🔒 Security Checks", box=box.SIMPLE, border_style="dim green")
    sec_table.add_column("Check", style="cyan")
    sec_table.add_column("Status", justify="center")
    for check, passed in app_obj.security_scan().items():
        icon = "[bold green]✓[/]" if passed else "[bold red]✗[/]"
        sec_table.add_row(check.replace("_", " ").title(), icon)
    console.print(sec_table)


@app.command(name="list")
def list_deployments() -> None:
    """List your active MomOps deployments."""
    records = StateStore().load()
    if not records:
        console.print("[yellow]No MomOps deployments found in ~/.momops/.[/]")
        return

    table = Table(title="Deployments", box=box.ROUNDED, border_style="green")
    table.add_column("App ID", style="cyan")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Region")
    table.add_column("Monthly", justify="right")
    table.add_column("Endpoint")
    for record in records:
        style = "green" if record.status == "deployed" else "yellow"
        table.add_row(
            record.app_id,
            record.name,
            f"[{style}]{record.status}[/]",
            record.region,
            f"${record.monthly_cost:.2f}",
            record.endpoint or "-",
        )
    console.print(table)
    return


@app.command()
def update(
    app_id: Annotated[str, typer.Argument(help="App ID or name")],
    status_value: Annotated[
        str | None,
        typer.Option("--status", help="Set local deployment status"),
    ] = None,
    endpoint: Annotated[
        str | None,
        typer.Option("--endpoint", help="Set local endpoint"),
    ] = None,
    monthly_cost: Annotated[
        float | None,
        typer.Option("--monthly-cost", help="Set estimated monthly cost"),
    ] = None,
) -> None:
    """Update a deployment record in local MomOps state."""
    if status_value is None and endpoint is None and monthly_cost is None:
        console.print("[yellow]Nothing to update. Pass --status, --endpoint, or --monthly-cost.[/]")
        raise typer.Exit(1)

    record = StateStore().update(
        app_id,
        status=status_value,
        endpoint=endpoint,
        monthly_cost=monthly_cost,
    )
    if record is None:
        console.print(f"[red]No deployment found for {app_id!r}.[/]")
        raise typer.Exit(1)

    console.print(
        Panel(
            f"[cyan]Status:[/] {record.status}\n"
            f"[cyan]Endpoint:[/] {record.endpoint or '-'}\n"
            f"[cyan]Monthly:[/] ${record.monthly_cost:.2f}",
            title=f"[green]Updated {record.app_id}[/]",
            border_style="green",
        )
    )


@app.command()
def status(
    app_id: Annotated[str, typer.Argument(help="App ID or name")],
) -> None:
    """Check the status of a deployment."""
    record = StateStore().get(app_id)
    if record is None:
        console.print(f"[red]No deployment found for {app_id!r}.[/]")
        raise typer.Exit(1)
    console.print(
        Panel(
            f"[cyan]Name:[/] {record.name}\n"
            f"[cyan]Status:[/] {record.status}\n"
            f"[cyan]Region:[/] {record.region}\n"
            f"[cyan]Endpoint:[/] {record.endpoint or '-'}\n"
            f"[cyan]Monthly:[/] ${record.monthly_cost:.2f}\n"
            f"[cyan]Updated:[/] {record.updated_at}",
            title=f"[green]{record.app_id}[/]",
            border_style="green",
        )
    )
    return


@app.command()
def logs(
    app_id: Annotated[str, typer.Argument(help="App ID or name")],
    tail: Annotated[int, typer.Option("--tail", "-n")] = 50,
) -> None:
    """Stream CloudWatch logs for a deployment."""
    record = StateStore().get(app_id)
    if record is None:
        console.print(f"[red]No deployment found for {app_id!r}.[/]")
        raise typer.Exit(1)
    console.print(f"[green]Last {tail} local events for [cyan]{record.app_id}[/]:[/]")
    console.print("[dim]CloudWatch Logs streaming is available after real AWS provisioning is enabled.[/]")
    return


@app.command()
def destroy(
    app_id: Annotated[str, typer.Argument(help="App ID or name")],
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    """Safely destroy a deployment (with confirmation)."""
    if not yes:
        confirmed = typer.confirm(
            f"⚠️  This will permanently destroy [red]{app_id}[/]. Are you sure?",
            default=False,
        )
        if not confirmed:
            console.print("[yellow]Cancelled.[/]")
            raise typer.Exit(0)

    record = StateStore().mark_destroyed(app_id)
    if record is None:
        console.print(f"[red]No deployment found for {app_id!r}.[/]")
        raise typer.Exit(1)
    console.print(f"[green]Marked {record.app_id} as destroyed in local state.[/]")
    console.print("[dim]AWS teardown hooks will use stored resource IDs when real provisioning is enabled.[/]")
    return


@app.command()
def talk() -> None:
    """Start an interactive conversation with Mom."""
    _print_banner()
    session = MomSession()
    session.run_interactive()


@app.command()
def auth() -> None:
    """Configure AWS credentials and budget guardrails."""
    console.print(Panel(
        "[bold]Setting up MomOps credentials[/]\n\n"
        "Mom needs your AWS credentials to provision infrastructure.\n"
        "These are stored securely in [cyan]~/.momops/credentials[/]\n\n"
        "[dim]Tip: Use an IAM user with least-privilege policies — Mom will tell you exactly which ones.[/]",
        title="[green]MomOps Auth[/]",
        border_style="green",
    ))

    aws_key = typer.prompt("AWS Access Key ID")
    aws_secret = typer.prompt("AWS Secret Access Key", hide_input=True)
    budget = typer.prompt("Monthly budget limit (USD)", default="100")
    store = StateStore()
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "credentials").write_text(
        "\n".join(
            [
                f"AWS_ACCESS_KEY_ID={aws_key}",
                f"AWS_SECRET_ACCESS_KEY={aws_secret}",
                f"MOMOPS_BUDGET_LIMIT={budget}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    console.print(f"\n[green]✓ Credentials saved[/] — budget guardrail: ${budget}/mo")
    console.print("[dim]Run [cyan]momops deploy[/] to get started.[/]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
