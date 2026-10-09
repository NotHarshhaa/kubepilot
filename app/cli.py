"""KubePilot Interactive CLI — terminal interface for Kubernetes diagnostics and AI operations."""

import argparse
import uuid

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from app.agent.graph import run_agent, stream_agent
from app.config.settings import get_settings
from app.reporting import generate_post_mortem
from app.tools.approvals import ApprovalError, manager
from app.tools.kubernetes.tools import cluster_health_scan

console = Console()

BANNER = r"""[bold cyan]
 _  __     _            ____  _ _       _
| |/ /   _| |__   ___  |  _ \(_) | ___ | |_
| ' / | | | '_ \ / _ \ | |_) | | |/ _ \| __|
| . \ |_| | |_) |  __/ |  __/| | | (_) | |_
|_|\_\__,_|_.__/ \___| |_|   |_|_|\___/ \__|
[/bold cyan]
[dim]AI-Powered Kubernetes Troubleshooting & Operations Agent[/dim]
"""


def _print_welcome(session_id: str) -> None:
    settings = get_settings()
    console.print(BANNER)
    info = (
        f"[bold]Provider:[/bold] {settings.llm_provider} ([dim]{settings.model_name}[/dim])  "
        f"[bold]Default Namespace:[/bold] {settings.default_namespace}  "
        f"[bold]Approval Required:[/bold] {settings.require_approval}  "
        f"[bold]Session ID:[/bold] {session_id}"
    )
    console.print(Panel(info, title="Cluster & Agent Status", border_style="cyan"))
    console.print(
        "[dim]Type your question in natural language. Type '/help' for commands, 'exit' to quit.[/dim]\n"
    )


def cmd_scan() -> None:
    """Run cluster-wide health triage."""
    with console.status("[bold green]Scanning cluster across all namespaces...", spinner="dots"):
        report = cluster_health_scan()
    console.print(Panel(report, title="Cluster Health Triage", border_style="green"))


def cmd_runbook(pod_name: str = "", namespace: str = "", runbook_type: str = "auto") -> None:
    """Run an automated diagnostic runbook."""
    from app.diagnostics.runbooks import run_diagnostic_runbook

    target_desc = (
        f"pod '{pod_name}'" if pod_name else f"unhealthy workloads in namespace '{namespace or 'default'}'"
    )
    with console.status(
        f"[bold cyan]Executing {runbook_type} diagnostic runbook for {target_desc}...", spinner="dots"
    ):
        report = run_diagnostic_runbook(pod_name=pod_name, namespace=namespace, runbook_type=runbook_type)
    console.print(Markdown(report))


def cmd_helm(namespace: str = "") -> None:
    """List Helm releases."""
    from app.tools.gitops.helm import helm_list

    with console.status("[bold cyan]Querying Helm releases...", spinner="dots"):
        output = helm_list(namespace=namespace)
    console.print(Panel(output, title="Helm Releases", border_style="cyan"))


def cmd_argocd(namespace: str = "argocd", app_name: str = "") -> None:
    """List or inspect Argo CD applications."""
    from app.tools.gitops.argocd import get_argocd_apps

    with console.status("[bold cyan]Querying Argo CD applications...", spinner="dots"):
        output = get_argocd_apps(namespace=namespace, app_name=app_name)
    console.print(Panel(output, title="Argo CD Applications", border_style="cyan"))


def cmd_mcp() -> None:
    """Launch Model Context Protocol (MCP) server over stdio."""
    from app.mcp.server import run_stdio_server

    run_stdio_server()


def cmd_approvals_list() -> None:
    """List pending and historical approvals."""
    pending = manager.list_pending()
    all_records = manager.list_all()

    if not all_records:
        console.print("[yellow]No approval requests found in this session.[/yellow]")
        return

    table = Table(title="KubePilot Approvals", border_style="cyan")
    table.add_column("ID", style="bold cyan")
    table.add_column("Tool", style="magenta")
    table.add_column("Arguments", style="white")
    table.add_column("Status", style="bold")
    table.add_column("Requested At", style="dim")
    table.add_column("Result / Note", style="italic")

    for rec in all_records:
        status_style = {
            "pending": "[bold yellow]PENDING[/bold yellow]",
            "approved": "[bold green]APPROVED[/bold green]",
            "denied": "[bold red]DENIED[/bold red]",
        }.get(rec.status, rec.status)

        res_note = rec.result or rec.note or ""
        if len(res_note) > 40:
            res_note = res_note[:37] + "..."
        table.add_row(rec.id, rec.tool, str(rec.args), status_style, rec.requested_at[:19], res_note)

    console.print(table)
    if pending:
        console.print(f"\n[bold yellow]{len(pending)} action(s) pending approval.[/bold yellow]")


def cmd_approvals_approve(approval_id: str, note: str | None = None) -> None:
    """Approve a pending action."""
    try:
        record = manager.approve(approval_id, note=note)
        console.print(
            f"[bold green]✔ Action '{record.tool}' ({approval_id}) approved and executed![/bold green]"
        )
        if record.result:
            console.print(Panel(record.result, title="Execution Output", border_style="green"))
    except ApprovalError as exc:
        console.print(f"[bold red]Approval failed: {exc}[/bold red]")


def cmd_approvals_deny(approval_id: str, note: str | None = None) -> None:
    """Deny a pending action."""
    try:
        record = manager.deny(approval_id, note=note)
        console.print(f"[bold yellow]✘ Action '{record.tool}' ({approval_id}) denied.[/bold yellow]")
    except ApprovalError as exc:
        console.print(f"[bold red]Denial failed: {exc}[/bold red]")


def _handle_question(question: str, session_id: str) -> None:
    """Execute question and display streaming or result."""
    console.print()
    pending_before = {r.id for r in manager.list_pending()}

    with console.status("[bold blue]KubePilot analyzing cluster...", spinner="aesthetic") as status:
        try:
            tools_called = []
            for event in stream_agent(question, session_id=session_id):
                etype = event.get("type")
                if etype == "tool_call":
                    t_name = event.get("tool")
                    args = event.get("args")
                    tools_called.append(t_name)
                    status.update(f"[bold cyan]Tool [{t_name}][/bold cyan] {args}")
                    console.print(f"  [dim]↳ Invoking [cyan]{t_name}[/cyan]({args})[/dim]")
                elif etype == "done":
                    answer = event.get("answer", "")
                    console.print("\n[bold green]Diagnosis & Recommendation:[/bold green]")
                    console.print(Markdown(answer))
                    break
        except Exception:
            # Fallback to run_agent if stream fails
            try:
                res = run_agent(question, session_id=session_id)
                console.print("\n[bold green]Diagnosis & Recommendation:[/bold green]")
                console.print(Markdown(res["answer"]))
                if res.get("tools_used"):
                    console.print(f"[dim]Tools used: {', '.join(res['tools_used'])}[/dim]")
            except Exception as inner_exc:
                console.print(f"[bold red]Error running agent:[/bold red] {inner_exc}")
                return

    # Check if any new approval request was generated
    pending_after = manager.list_pending()
    new_pending = [r for r in pending_after if r.id not in pending_before]
    for p in new_pending:
        console.print()
        console.print(
            Panel(
                f"[bold yellow]Operational Action Proposed:[/bold yellow] [bold cyan]{p.tool}[/bold cyan]\n"
                f"Arguments: `{p.args}`\n"
                f"Approval ID: [bold]{p.id}[/bold]",
                title="Action Approval Required",
                border_style="yellow",
            )
        )
        if Confirm.ask(f"Do you want to approve and execute '{p.tool}' now?"):
            cmd_approvals_approve(p.id, note="Approved interactively via KubePilot CLI")


def cmd_report_interactive() -> None:
    """Interactively generate an incident post-mortem report."""
    console.print(Panel("[bold]KubePilot Incident Post-Mortem Generator[/bold]", border_style="cyan"))
    title = Prompt.ask("Incident Title", default="API Gateway Degradation and Container Restarts")
    severity = Prompt.ask(
        "Severity", choices=["P1 - Critical", "P2 - Major", "P3 - Moderate", "P4 - Low"], default="P2 - Major"
    )
    namespace = Prompt.ask("Namespace", default="default")
    services = Prompt.ask("Impacted Services (comma-separated)", default="nginx-ingress, auth-api").split(",")
    summary = Prompt.ask(
        "Executive Summary",
        default="Cascading CrashLoopBackOff triggered by bad configuration and memory exhaustion.",
    )
    root_cause = Prompt.ask(
        "Root Cause",
        default="OOMKilled due to missing memory limits coupled with database reconnection storm.",
    )

    report = generate_post_mortem(
        title=title,
        summary=summary,
        root_cause=root_cause,
        severity=severity,
        namespace=namespace,
        impacted_services=[s.strip() for s in services],
    )

    console.print("\n" + "=" * 60)
    console.print(Markdown(report))
    console.print("=" * 60 + "\n")

    filename = Prompt.ask("Save report to file? (leave empty to skip)", default="incident-report.md")
    if filename.strip():
        with open(filename, "w", encoding="utf-8") as f:
            f.write(report)
        console.print(f"[bold green]✔ Report saved to {filename}[/bold green]")


def repl() -> None:
    """Interactive CLI REPL loop."""
    session_id = uuid.uuid4().hex[:8]
    _print_welcome(session_id)

    while True:
        try:
            user_input = Prompt.ask("[bold cyan]kubepilot[/bold cyan]").strip()
            if not user_input:
                continue

            low = user_input.lower()
            if low in ("exit", "quit", "q"):
                console.print("[dim]Goodbye![/dim]")
                break
            elif low in ("/help", "help"):
                console.print(
                    Panel(
                        "- [bold]Ask any question[/bold]: 'why is checkout pod restarting in prod?'\n"
                        "- [bold]/scan[/bold]: Instant cluster-wide health check across all namespaces\n"
                        "- [bold]/runbook [pod][/bold]: Run automated diagnostic runbook for pod failures\n"
                        "- [bold]/helm [ns][/bold]: List Helm releases in namespace\n"
                        "- [bold]/argocd [app][/bold]: Inspect Argo CD GitOps applications\n"
                        "- [bold]/approvals[/bold]: View pending and executed operational actions\n"
                        "- [bold]/approve <id>[/bold]: Approve and execute an action\n"
                        "- [bold]/deny <id>[/bold]: Deny an action\n"
                        "- [bold]/report[/bold]: Generate an incident post-mortem markdown report\n"
                        "- [bold]/clear[/bold]: Clear the screen\n"
                        "- [bold]exit[/bold]: Exit KubePilot CLI",
                        title="Available Commands",
                        border_style="cyan",
                    )
                )
            elif low == "/scan":
                cmd_scan()
            elif low.startswith("/runbook"):
                parts = user_input.split(maxsplit=2)
                pod_target = parts[1] if len(parts) > 1 else ""
                cmd_runbook(pod_name=pod_target)
            elif low.startswith("/helm"):
                parts = user_input.split(maxsplit=2)
                ns_target = parts[1] if len(parts) > 1 else ""
                cmd_helm(namespace=ns_target)
            elif low.startswith("/argocd"):
                parts = user_input.split(maxsplit=2)
                app_target = parts[1] if len(parts) > 1 else ""
                cmd_argocd(app_name=app_target)
            elif low == "/approvals":
                cmd_approvals_list()
            elif low.startswith("/approve"):
                parts = user_input.split()
                if len(parts) > 1:
                    cmd_approvals_approve(parts[1])
                else:
                    console.print("[yellow]Usage: /approve <approval_id>[/yellow]")
            elif low.startswith("/deny"):
                parts = user_input.split()
                if len(parts) > 1:
                    cmd_approvals_deny(parts[1])
                else:
                    console.print("[yellow]Usage: /deny <approval_id>[/yellow]")
            elif low == "/report":
                cmd_report_interactive()
            elif low == "/clear":
                console.clear()
                _print_welcome(session_id)
            else:
                _handle_question(user_input, session_id)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Session terminated.[/dim]")
            break


def main() -> None:
    parser = argparse.ArgumentParser(description="KubePilot CLI — AI-powered Kubernetes diagnostics.")
    subparsers = parser.add_subparsers(dest="command")

    # kubepilot ask "<question>"
    ask_parser = subparsers.add_parser("ask", help="Ask a one-off troubleshooting question")
    ask_parser.add_argument("question", help="Natural language question")
    ask_parser.add_argument("--session", default=None, help="Optional session ID")

    # kubepilot scan
    subparsers.add_parser("scan", help="Run cluster-wide health triage")

    # kubepilot runbook [pod]
    rb_parser = subparsers.add_parser("runbook", help="Run automated diagnostic runbook")
    rb_parser.add_argument("pod", nargs="?", default="", help="Optional pod name to diagnose")
    rb_parser.add_argument("--namespace", "-n", default="", help="Kubernetes namespace")
    rb_parser.add_argument(
        "--type",
        "-t",
        default="auto",
        choices=["auto", "crashloop", "oom", "imagepull", "pending"],
        help="Runbook type",
    )

    # kubepilot helm
    helm_parser = subparsers.add_parser("helm", help="Inspect Helm releases")
    helm_parser.add_argument("--namespace", "-n", default="", help="Kubernetes namespace")

    # kubepilot argocd
    argocd_parser = subparsers.add_parser("argocd", help="Inspect Argo CD applications")
    argocd_parser.add_argument("--app", default="", help="Optional specific application name")
    argocd_parser.add_argument("--namespace", "-n", default="argocd", help="Argo CD namespace")

    # kubepilot mcp
    subparsers.add_parser("mcp", help="Start Model Context Protocol (MCP) server over stdio")

    # kubepilot approvals
    appr_parser = subparsers.add_parser("approvals", help="Manage operational approvals")
    appr_sub = appr_parser.add_subparsers(dest="approvals_action")
    appr_sub.add_parser("list", help="List all approvals")
    appr_approve = appr_sub.add_parser("approve", help="Approve an action")
    appr_approve.add_argument("id", help="Approval ID")
    appr_approve.add_argument("--note", default=None, help="Optional approval note")
    appr_deny = appr_sub.add_parser("deny", help="Deny an action")
    appr_deny.add_argument("id", help="Approval ID")
    appr_deny.add_argument("--note", default=None, help="Optional denial note")

    # kubepilot report
    subparsers.add_parser("report", help="Generate incident post-mortem report")

    # kubepilot chat
    subparsers.add_parser("chat", help="Start interactive chat REPL")

    args = parser.parse_args()

    if args.command == "ask":
        sid = args.session or uuid.uuid4().hex[:8]
        _handle_question(args.question, sid)
    elif args.command == "scan":
        cmd_scan()
    elif args.command == "runbook":
        cmd_runbook(pod_name=args.pod, namespace=args.namespace, runbook_type=args.type)
    elif args.command == "helm":
        cmd_helm(namespace=args.namespace)
    elif args.command == "argocd":
        cmd_argocd(namespace=args.namespace, app_name=args.app)
    elif args.command == "mcp":
        cmd_mcp()
    elif args.command == "approvals":
        if args.approvals_action == "approve":
            cmd_approvals_approve(args.id, note=args.note)
        elif args.approvals_action == "deny":
            cmd_approvals_deny(args.id, note=args.note)
        else:
            cmd_approvals_list()
    elif args.command == "report":
        cmd_report_interactive()
    else:
        # Default with no arguments or "chat": launch REPL
        repl()


if __name__ == "__main__":
    main()
