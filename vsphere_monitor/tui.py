"""Textual TUI for interactive vSphere Prow job monitoring."""

from __future__ import annotations

import re
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, RichLog, Static
from textual.worker import Worker, WorkerState

from rich.text import Text

from vsphere_monitor.analyzer import JobSummary, analyze
from vsphere_monitor.fetcher import fetch, fetch_build_log
from vsphere_monitor.formatters import filter_summaries, sort_summaries

# State display mapping
_STATE_DISPLAY: dict[str, str] = {
    "success": "OK",
    "failure": "FAIL",
    "pending": "PEND",
    "aborted": "ABRT",
    "error": "ERR",
    "triggered": "TRIG",
}

_SORT_KEYS = ["recent", "version", "failure_rate", "state"]
_STATE_FILTERS = [None, "failure", "success", "pending", "aborted", "error"]

# Patterns to highlight as errors in build logs
_ERROR_RE = re.compile(
    r"(error|ERROR|FAIL|FAILED|fatal|FATAL|panic|PANIC|timed?\s*out"
    r"|DeadlineExceeded|could not|cannot|exit\s+code\s+[1-9])",
)


def _sparkline_plain(summary: JobSummary) -> str:
    mapping = {"success": "S", "failure": "F", "pending": "P",
               "aborted": "A", "error": "E", "triggered": "T"}
    return "".join(mapping.get(s, "?") for s in summary.recent_states)


# ---------------------------------------------------------------------------
# Log viewer screen
# ---------------------------------------------------------------------------

class LogViewerScreen(Screen):
    """Full-screen build log viewer with error highlighting."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back to jobs"),
        Binding("q", "app.pop_screen", "Back to jobs"),
    ]

    def __init__(self, job_name: str, prow_url: str) -> None:
        super().__init__()
        self._job_name = job_name
        self._prow_url = prow_url

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(f"Loading log for {self._job_name}...", id="log-status")
        yield RichLog(max_lines=5000, wrap=False, highlight=False, markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._fetch_log(), name="fetch_log", exclusive=True)

    async def _fetch_log(self) -> tuple[str, list[str]]:
        return fetch_build_log(self._prow_url)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "fetch_log":
            return

        status = self.query_one("#log-status", Static)
        log_widget = self.query_one(RichLog)

        if event.state == WorkerState.ERROR:
            error = event.worker.error
            status.update(f"[red]Error fetching log: {error}[/]")
            return

        if event.state == WorkerState.SUCCESS:
            log_url, lines = event.worker.result
            status.update(
                f"[dim]{self._job_name}  |  {len(lines)} lines  |  {log_url}[/]"
            )

            for line in lines:
                if _ERROR_RE.search(line):
                    styled = Text(line, style="red")
                    log_widget.write(styled)
                else:
                    log_widget.write(line)


# ---------------------------------------------------------------------------
# Stats bar
# ---------------------------------------------------------------------------

class StatsBar(Static):
    """Displays aggregate stats and active filters."""

    def __init__(self, summaries: list[JobSummary]) -> None:
        super().__init__()
        self._all = summaries
        self._version_filter: str | None = None
        self._state_filter: str | None = None
        self._sort_by: str = "recent"

    def set_summaries(self, summaries: list[JobSummary]) -> None:
        self._all = summaries
        self._refresh_text()

    def update_filters(
        self,
        version_filter: str | None,
        state_filter: str | None,
        sort_by: str,
    ) -> None:
        self._version_filter = version_filter
        self._state_filter = state_filter
        self._sort_by = sort_by
        self._refresh_text()

    def on_mount(self) -> None:
        self._refresh_text()

    def _refresh_text(self) -> None:
        total = len(self._all)
        passing = sum(1 for s in self._all if s.latest_state == "success")
        failing = sum(1 for s in self._all if s.latest_state == "failure")
        pending = sum(1 for s in self._all if s.latest_state == "pending")

        parts = [
            f"Total: {total}",
            f"Pass: {passing}",
            f"Fail: {failing}",
            f"Pending: {pending}",
        ]

        filters: list[str] = []
        if self._version_filter:
            filters.append(f"ver={self._version_filter}")
        if self._state_filter:
            filters.append(f"state={self._state_filter}")
        if filters:
            parts.append(f"Filter: {', '.join(filters)}")
        parts.append(f"Sort: {self._sort_by}")

        self.update("  |  ".join(parts))


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

class ProwMonitorApp(App):
    """Textual app for browsing vSphere Prow periodic jobs."""

    TITLE = "vSphere Periodic Job Monitor"

    CSS = """
    StatsBar {
        dock: top;
        height: 1;
        background: $primary-background;
        color: $text;
        padding: 0 1;
    }
    DataTable {
        height: 1fr;
    }
    #log-status {
        dock: top;
        height: 1;
        background: $primary-background;
        color: $text;
        padding: 0 1;
    }
    RichLog {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("r", "reload", "Reload data"),
        Binding("v", "cycle_version", "Cycle version"),
        Binding("s", "cycle_state", "Cycle state"),
        Binding("o", "cycle_sort", "Cycle sort"),
        Binding("c", "clear_filters", "Clear filters"),
        Binding("escape", "quit", "Quit"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        summaries: list[JobSummary],
        file_path: str | Path | None = None,
    ) -> None:
        super().__init__()
        self._all_summaries = summaries
        self._file_path = file_path
        self._rebuild_versions()
        self._version_idx = 0
        self._state_idx = 0
        self._sort_idx = 0
        self._displayed: list[JobSummary] = []

    def _rebuild_versions(self) -> None:
        self._versions: list[str | None] = [None] + sorted(
            set(s.ocp_version for s in self._all_summaries),
            key=lambda v: (v == "unknown", v),
        )

    @property
    def _version_filter(self) -> str | None:
        return self._versions[self._version_idx]

    @property
    def _state_filter(self) -> str | None:
        return _STATE_FILTERS[self._state_idx]

    @property
    def _sort_by(self) -> str:
        return _SORT_KEYS[self._sort_idx]

    def compose(self) -> ComposeResult:
        yield Header()
        yield StatsBar(self._all_summaries)
        yield DataTable(cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(
            "VER", "STATUS", "RECENT", "FAIL%", "LAST OK",
            "TYPE", "JOB NAME", "URL",
        )
        self._refresh_table()

    def _refresh_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()

        filtered = filter_summaries(
            self._all_summaries, self._version_filter, self._state_filter
        )
        self._displayed = sort_summaries(filtered, self._sort_by)

        for s in self._displayed:
            short_url = s.latest_url
            if "test-platform-results/logs/" in short_url:
                short_url = short_url.split("test-platform-results/logs/")[-1]

            table.add_row(
                s.ocp_version,
                _STATE_DISPLAY.get(s.latest_state, s.latest_state.upper()),
                _sparkline_plain(s),
                f"{s.failure_rate:.0%}",
                s.last_success_age,
                s.job_variant,
                s.job,
                short_url,
            )

        self.query_one(StatsBar).update_filters(
            self._version_filter, self._state_filter, self._sort_by
        )

    # -- Enter: open build log in viewer screen ----------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_idx = event.cursor_row
        if 0 <= row_idx < len(self._displayed):
            summary = self._displayed[row_idx]
            url = summary.latest_url
            if url:
                self.push_screen(LogViewerScreen(summary.job, url))

    # -- r: reload data ----------------------------------------------------

    def action_reload(self) -> None:
        self.notify("Reloading data...")
        self.run_worker(self._do_reload(), name="reload", exclusive=True)

    async def _do_reload(self) -> list[JobSummary]:
        raw = fetch(file=self._file_path, refresh=True)
        return analyze(raw)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "reload":
            return

        if event.state == WorkerState.ERROR:
            self.notify(f"Reload failed: {event.worker.error}", severity="error")
            return

        if event.state == WorkerState.SUCCESS:
            self._all_summaries = event.worker.result
            self._rebuild_versions()
            # Clamp filter indices in case versions changed
            self._version_idx = min(self._version_idx, len(self._versions) - 1)
            self.query_one(StatsBar).set_summaries(self._all_summaries)
            self._refresh_table()
            self.notify(
                f"Reloaded: {len(self._all_summaries)} jobs", severity="information"
            )

    # -- filter/sort actions -----------------------------------------------

    def action_cycle_version(self) -> None:
        self._version_idx = (self._version_idx + 1) % len(self._versions)
        self._refresh_table()

    def action_cycle_state(self) -> None:
        self._state_idx = (self._state_idx + 1) % len(_STATE_FILTERS)
        self._refresh_table()

    def action_cycle_sort(self) -> None:
        self._sort_idx = (self._sort_idx + 1) % len(_SORT_KEYS)
        self._refresh_table()

    def action_clear_filters(self) -> None:
        self._version_idx = 0
        self._state_idx = 0
        self._sort_idx = 0
        self._refresh_table()


def run_interactive(
    summaries: list[JobSummary],
    file_path: str | Path | None = None,
) -> None:
    """Launch the Textual TUI app."""
    app = ProwMonitorApp(summaries, file_path=file_path)
    app.run()
