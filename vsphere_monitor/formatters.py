"""Output formatters: Rich table, JSON, and interactive TUI."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from vsphere_monitor.analyzer import JobSummary

console = Console()

# State -> (color, symbol)
_STATE_STYLE: dict[str, tuple[str, str]] = {
    "success": ("green", "OK"),
    "failure": ("red", "FAIL"),
    "pending": ("yellow", "PEND"),
    "aborted": ("dim", "ABRT"),
    "error": ("red bold", "ERR"),
    "triggered": ("cyan", "TRIG"),
}


def _styled_state(state: str) -> Text:
    color, label = _STATE_STYLE.get(state, ("white", state.upper()))
    return Text(label, style=color)


def _sparkline(summary: JobSummary) -> Text:
    """Colored sparkline of recent run states."""
    t = Text()
    for s in summary.recent_states:
        color, _ = _STATE_STYLE.get(s, ("white", "?"))
        char = s[0].upper()
        t.append(char, style=color)
    return t


def _fail_rate_text(rate: float) -> Text:
    pct = f"{rate:.0%}"
    if rate >= 0.75:
        return Text(pct, style="red bold")
    if rate >= 0.5:
        return Text(pct, style="red")
    if rate >= 0.25:
        return Text(pct, style="yellow")
    return Text(pct, style="green")


def sort_summaries(
    summaries: list[JobSummary],
    sort_by: str = "recent",
) -> list[JobSummary]:
    """Sort summaries by the given key. Returns a new sorted list."""
    result = list(summaries)
    if sort_by == "recent":
        result.sort(key=lambda s: s.latest_run.start_time, reverse=True)
    elif sort_by == "failure_rate":
        result.sort(key=lambda s: s.failure_rate, reverse=True)
    elif sort_by == "version":
        result.sort(key=lambda s: (s.ocp_version == "unknown", s.ocp_version, s.job))
    elif sort_by == "state":
        state_order = {"failure": 0, "error": 1, "pending": 2, "aborted": 3, "success": 4}
        result.sort(key=lambda s: (state_order.get(s.latest_state, 5), s.job))
    return result


def filter_summaries(
    summaries: list[JobSummary],
    version_filter: str | None = None,
    state_filter: str | None = None,
) -> list[JobSummary]:
    """Filter summaries by version and/or state."""
    result = summaries
    if version_filter:
        result = [s for s in result if s.ocp_version == version_filter]
    if state_filter:
        result = [s for s in result if s.latest_state == state_filter]
    return result


def _build_table(
    summaries: list[JobSummary],
    version_filter: str | None = None,
    state_filter: str | None = None,
    sort_by: str = "recent",
) -> Table:
    """Build a Rich Table from job summaries."""
    filtered = filter_summaries(summaries, version_filter, state_filter)
    filtered = sort_summaries(filtered, sort_by)

    table = Table(
        title="vSphere Periodic Job Monitor",
        show_lines=False,
        pad_edge=False,
    )
    table.add_column("VER", style="cyan", no_wrap=True)
    table.add_column("STATUS", no_wrap=True)
    table.add_column("RECENT", no_wrap=True)
    table.add_column("FAIL%", no_wrap=True)
    table.add_column("LAST OK", no_wrap=True)
    table.add_column("TYPE", style="dim", no_wrap=True)
    table.add_column("JOB NAME", no_wrap=True)
    table.add_column("URL", style="dim", no_wrap=True)

    current_version = None
    for s in filtered:
        if sort_by == "version" and s.ocp_version != current_version:
            current_version = s.ocp_version
            if table.row_count > 0:
                table.add_section()

        # Shorten URL for display
        short_url = s.latest_url
        if "test-platform-results/logs/" in short_url:
            short_url = short_url.split("test-platform-results/logs/")[-1]

        table.add_row(
            s.ocp_version,
            _styled_state(s.latest_state),
            _sparkline(s),
            _fail_rate_text(s.failure_rate),
            s.last_success_age,
            s.job_variant,
            s.job,
            short_url,
        )

    return table


def print_table(
    summaries: list[JobSummary],
    version_filter: str | None = None,
    state_filter: str | None = None,
    sort_by: str = "recent",
) -> None:
    """Print the formatted table to the console."""
    table = _build_table(summaries, version_filter, state_filter, sort_by)

    # Print summary header
    total = len(summaries)
    failing = sum(1 for s in summaries if s.latest_state == "failure")
    passing = sum(1 for s in summaries if s.latest_state == "success")
    pending = sum(1 for s in summaries if s.latest_state == "pending")

    console.print()
    console.print(
        f"[bold]Total:[/] {total}  "
        f"[green]Pass:[/] {passing}  "
        f"[red]Fail:[/] {failing}  "
        f"[yellow]Pending:[/] {pending}"
    )
    console.print()
    console.print(table)


def print_json(
    summaries: list[JobSummary],
    version_filter: str | None = None,
    state_filter: str | None = None,
) -> None:
    """Print JSON output for machine consumption."""
    filtered = summaries
    if version_filter:
        filtered = [s for s in filtered if s.ocp_version == version_filter]
    if state_filter:
        filtered = [s for s in filtered if s.latest_state == state_filter]

    output: list[dict[str, Any]] = []
    for s in filtered:
        output.append({
            "job": s.job,
            "ocp_version": s.ocp_version,
            "variant": s.job_variant,
            "latest_state": s.latest_state,
            "failure_rate": round(s.failure_rate, 3),
            "total_runs": s.total_runs,
            "failure_count": s.failure_count,
            "last_success_age": s.last_success_age,
            "recent_states": s.recent_states,
            "latest_url": s.latest_url,
        })

    print(json.dumps(output, indent=2))



