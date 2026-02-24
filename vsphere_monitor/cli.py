"""CLI entry point for the vSphere Prow Job Monitor."""

from __future__ import annotations

import argparse
import sys

from rich.console import Console

from vsphere_monitor.fetcher import fetch
from vsphere_monitor.analyzer import analyze, build_compact_summary
from vsphere_monitor.formatters import print_table, print_json
from vsphere_monitor.tui import run_interactive
from vsphere_monitor.llm import ask

console = Console(stderr=True)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vsphere-monitor",
        description="Monitor vSphere periodic Prow CI jobs",
    )

    # Data source
    p.add_argument(
        "--file", "-f",
        metavar="PATH",
        help="Read from a local prowjobs.json instead of the Prow API",
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="Force refresh, ignoring the 30-minute cache",
    )

    # Output mode (mutually exclusive)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        dest="output_format",
        help="Output format (default: table)",
    )
    mode.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="Launch interactive TUI mode",
    )
    mode.add_argument(
        "--ask", "-a",
        metavar="QUESTION",
        help="Ask a natural language question about job status (requires ANTHROPIC_API_KEY)",
    )
    mode.add_argument(
        "--summary",
        action="store_true",
        help="Print the compact text summary (useful for piping to an LLM)",
    )

    # Filters
    p.add_argument(
        "--version", "-v",
        metavar="VER",
        help="Filter by OCP version (e.g. 4.18)",
    )
    p.add_argument(
        "--state", "-s",
        choices=["success", "failure", "pending", "aborted", "error"],
        help="Filter by job state",
    )
    p.add_argument(
        "--sort",
        choices=["recent", "version", "failure_rate", "state"],
        default="recent",
        help="Sort order (default: recent)",
    )

    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Fetch data
    with console.status("[bold cyan]Fetching prow job data..."):
        try:
            raw = fetch(file=args.file, refresh=args.refresh)
        except Exception as e:
            console.print(f"[red bold]Error fetching data:[/] {e}")
            sys.exit(1)

    # Analyze
    with console.status("[bold cyan]Analyzing vSphere periodic jobs..."):
        summaries = analyze(raw)

    if not summaries:
        console.print("[yellow]No vSphere periodic jobs found in the data.[/]")
        sys.exit(0)

    console.print(
        f"[dim]Found {len(summaries)} unique vSphere periodic jobs[/]"
    )

    # Dispatch to output mode
    if args.interactive:
        run_interactive(summaries, file_path=args.file)
    elif args.ask:
        with console.status("[bold cyan]Asking Claude..."):
            answer = ask(summaries, args.ask)
        Console().print(answer)
    elif args.summary:
        print(build_compact_summary(summaries))
    elif args.output_format == "json":
        print_json(summaries, version_filter=args.version, state_filter=args.state)
    else:
        print_table(
            summaries,
            version_filter=args.version,
            state_filter=args.state,
            sort_by=args.sort,
        )
