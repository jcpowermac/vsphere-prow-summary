"""Textual TUI for interactive vSphere Prow job monitoring."""

from __future__ import annotations

import re
import webbrowser
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Checkbox, DataTable, Footer, Header, RichLog, Static
from textual.worker import Worker, WorkerState

from vsphere_monitor.analyzer import JobSummary, analyze
from vsphere_monitor.fetcher import fetch, fetch_build_log
from vsphere_monitor.formatters import filter_summaries, sort_summaries
from vsphere_monitor.installer import fetch_install_statuses_async

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

# Column registry: (key, display_label)
ALL_COLUMNS: list[tuple[str, str]] = [
    ("ver", "VER"),
    ("status", "STATUS"),
    ("install", "INSTALL"),
    ("recent", "RECENT"),
    ("fail_pct", "FAIL%"),
    ("last_ok", "LAST OK"),
    ("started", "STARTED"),
    ("duration", "DURATION"),
    ("type", "TYPE"),
    ("job_name", "JOB NAME"),
    ("url", "URL"),
]

# Patterns to highlight as errors in build logs
_ERROR_RE = re.compile(
    r"(error|ERROR|FAIL|FAILED|fatal|FATAL|panic|PANIC|timed?\s*out"
    r"|DeadlineExceeded|could not|cannot|exit\s+code\s+[1-9])",
)


def _sparkline_plain(summary: JobSummary) -> str:
    mapping = {
        "success": "S",
        "failure": "F",
        "pending": "P",
        "aborted": "A",
        "error": "E",
        "triggered": "T",
    }
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
            status.update(f"[dim]{self._job_name}  |  {len(lines)} lines  |  {log_url}[/]")

            for line in lines:
                if _ERROR_RE.search(line):
                    styled = Text(line, style="red")
                    log_widget.write(styled)
                else:
                    log_widget.write(line)


# ---------------------------------------------------------------------------
# Column visibility screen
# ---------------------------------------------------------------------------


class ColumnToggleScreen(ModalScreen[set[str]]):
    """Modal dialog for toggling column visibility."""

    CSS = """
    ColumnToggleScreen {
        align: center middle;
    }
    #col-toggle-container {
        width: 40;
        max-height: 20;
        background: $surface;
        border: tall $primary;
        padding: 1 2;
    }
    #col-toggle-title {
        text-align: center;
        text-style: bold;
        width: 100%;
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Done"),
        Binding("enter", "dismiss_screen", "Done"),
    ]

    def __init__(self, visible: set[str]) -> None:
        super().__init__()
        self._visible = set(visible)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="col-toggle-container"):
            yield Static("Toggle Columns", id="col-toggle-title")
            for key, label in ALL_COLUMNS:
                yield Checkbox(label, value=key in self._visible, id=f"col-{key}")

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        col_key = event.checkbox.id
        if col_key is None:
            return
        col_key = col_key.removeprefix("col-")
        if event.value:
            self._visible.add(col_key)
        else:
            # Prevent hiding all columns
            if len(self._visible) > 1:
                self._visible.discard(col_key)
            else:
                event.checkbox.value = True
                self.notify("At least one column must remain visible")

    def action_dismiss_screen(self) -> None:
        self.dismiss(self._visible)


# ---------------------------------------------------------------------------
# Version selection screen
# ---------------------------------------------------------------------------


class VersionSelectScreen(ModalScreen[set[str]]):
    """Modal dialog for selecting one or more OCP versions to filter by."""

    CSS = """
    VersionSelectScreen {
        align: center middle;
    }
    #ver-select-container {
        width: 40;
        max-height: 24;
        background: $surface;
        border: tall $primary;
        padding: 1 2;
    }
    #ver-select-title {
        text-align: center;
        text-style: bold;
        width: 100%;
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Done"),
        Binding("enter", "dismiss_screen", "Done"),
    ]

    def __init__(self, versions: list[str], selected: set[str]) -> None:
        super().__init__()
        self._versions = versions
        self._selected = set(selected)

    @staticmethod
    def _ver_to_id(ver: str) -> str:
        """Convert a version string to a valid Textual widget id."""
        return f"ver-{ver.replace('.', '-')}"

    @staticmethod
    def _id_to_ver(widget_id: str) -> str:
        """Convert a widget id back to a version string (e.g. 'ver-4-18' -> '4.18')."""
        raw = widget_id.removeprefix("ver-")
        # Re-insert the dot after the major version digit(s)
        parts = raw.split("-", 1)
        return ".".join(parts) if len(parts) == 2 else raw

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="ver-select-container"):
            yield Static("Filter Versions (empty = all)", id="ver-select-title")
            for ver in self._versions:
                yield Checkbox(ver, value=ver in self._selected, id=self._ver_to_id(ver))

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        cb_id = event.checkbox.id
        if cb_id is None:
            return
        ver = self._id_to_ver(cb_id)
        if event.value:
            self._selected.add(ver)
        else:
            self._selected.discard(ver)

    def action_dismiss_screen(self) -> None:
        self.dismiss(self._selected)


# ---------------------------------------------------------------------------
# Stats bar
# ---------------------------------------------------------------------------


class StatsBar(Static):
    """Displays aggregate stats and active filters."""

    def __init__(self, summaries: list[JobSummary]) -> None:
        super().__init__()
        self._all = summaries
        self._version_filter: list[str] | None = None
        self._state_filter: str | None = None
        self._sort_by: str = "recent"

    def set_summaries(self, summaries: list[JobSummary]) -> None:
        self._all = summaries
        self._refresh_text()

    def update_filters(
        self,
        version_filter: list[str] | None,
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
            filters.append(f"ver={','.join(sorted(self._version_filter))}")
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
        Binding("v", "select_versions", "Filter versions"),
        Binding("s", "cycle_state", "Cycle state"),
        Binding("o", "cycle_sort", "Cycle sort"),
        Binding("c", "clear_filters", "Clear filters"),
        Binding("w", "open_prow_url", "Open in browser"),
        Binding("h", "toggle_columns", "Columns"),
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
        self._selected_versions: set[str] = set()
        self._state_idx = 0
        self._sort_idx = 0
        self._displayed: list[JobSummary] = []
        self._visible_columns: set[str] = {key for key, _ in ALL_COLUMNS}

    def _rebuild_versions(self) -> None:
        self._available_versions: list[str] = sorted(
            set(s.ocp_version for s in self._all_summaries),
            key=lambda v: (v == "unknown", v),
        )

    @property
    def _version_filter(self) -> list[str] | None:
        """Return selected versions as a list, or None if nothing is selected (show all)."""
        return sorted(self._selected_versions) if self._selected_versions else None

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
        self._rebuild_table()

    def _row_data(self, s: JobSummary) -> dict[str, str]:
        """Build a dict of column key -> cell value for a single summary."""
        short_url = s.latest_url
        if "test-platform-results/logs/" in short_url:
            short_url = short_url.split("test-platform-results/logs/")[-1]
        return {
            "ver": s.ocp_version,
            "status": _STATE_DISPLAY.get(s.latest_state, s.latest_state.upper()),
            "install": s.install_status,
            "recent": _sparkline_plain(s),
            "fail_pct": f"{s.failure_rate:.0%}",
            "last_ok": s.last_success_age,
            "started": s.latest_start_display,
            "duration": s.latest_duration,
            "type": s.job_variant,
            "job_name": s.job,
            "url": short_url,
        }

    def _active_columns(self) -> list[tuple[str, str]]:
        """Return the ordered list of (key, label) for currently visible columns."""
        return [(k, lbl) for k, lbl in ALL_COLUMNS if k in self._visible_columns]

    def _rebuild_table(self) -> None:
        """Clear columns and rows, re-add visible columns, then populate rows."""
        table = self.query_one(DataTable)
        table.clear(columns=True)
        cols = self._active_columns()
        table.add_columns(*(lbl for _, lbl in cols))
        self._refresh_table()

    def _refresh_table(self) -> None:
        """Clear rows and re-populate based on current filters/sort."""
        table = self.query_one(DataTable)
        table.clear()

        filtered = filter_summaries(self._all_summaries, self._version_filter, self._state_filter)
        self._displayed = sort_summaries(filtered, self._sort_by)

        cols = self._active_columns()
        highlight_failures = self._state_filter is None
        for s in self._displayed:
            data = self._row_data(s)
            values = [data[key] for key, _ in cols]
            if highlight_failures and s.latest_state in ("failure", "error"):
                table.add_row(*(Text(v, style="red") for v in values))
            else:
                table.add_row(*values)

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
        self.sub_title = "[bold cyan]Reloading…[/]"
        self.notify("Reloading data...")
        self.run_worker(self._do_reload(), name="reload", exclusive=True)

    async def _do_reload(self) -> list[JobSummary]:
        raw = fetch(file=self._file_path, refresh=True)
        summaries = analyze(raw)
        statuses = await fetch_install_statuses_async(summaries)
        for s in summaries:
            s.install_status = statuses.get(s.job, "--")
        return summaries

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "reload":
            return

        # Clear reload indicator from header when worker finishes (success or error)
        self.sub_title = ""

        if event.state == WorkerState.ERROR:
            self.notify(f"Reload failed: {event.worker.error}", severity="error")
            return

        if event.state == WorkerState.SUCCESS:
            self._all_summaries = event.worker.result
            self._rebuild_versions()
            # Drop any selected versions that no longer exist in the data
            available = set(self._available_versions)
            self._selected_versions &= available
            self.query_one(StatsBar).set_summaries(self._all_summaries)
            self._refresh_table()
            self.notify(f"Reloaded: {len(self._all_summaries)} jobs", severity="information")

    # -- filter/sort actions -----------------------------------------------

    def action_select_versions(self) -> None:
        def _on_dismiss(result: set[str] | None) -> None:
            if result is not None:
                self._selected_versions = result
                self._refresh_table()

        self.push_screen(
            VersionSelectScreen(self._available_versions, self._selected_versions),
            callback=_on_dismiss,
        )

    def action_cycle_state(self) -> None:
        self._state_idx = (self._state_idx + 1) % len(_STATE_FILTERS)
        self._refresh_table()

    def action_cycle_sort(self) -> None:
        self._sort_idx = (self._sort_idx + 1) % len(_SORT_KEYS)
        self._refresh_table()

    def action_clear_filters(self) -> None:
        self._selected_versions = set()
        self._state_idx = 0
        self._sort_idx = 0
        self._refresh_table()

    # -- w: open prow URL in browser ---------------------------------------

    def action_open_prow_url(self) -> None:
        table = self.query_one(DataTable)
        row_idx = table.cursor_row
        if row_idx is not None and 0 <= row_idx < len(self._displayed):
            summary = self._displayed[row_idx]
            url = summary.latest_url
            if url:
                webbrowser.open(url)
                self.notify(f"Opened {summary.job} in browser")
            else:
                self.notify("No URL available for this job", severity="warning")
        else:
            self.notify("No row selected", severity="warning")

    # -- h: toggle column visibility ---------------------------------------

    def action_toggle_columns(self) -> None:
        def _on_dismiss(result: set[str] | None) -> None:
            if result is not None:
                self._visible_columns = result
                self._rebuild_table()

        self.push_screen(ColumnToggleScreen(self._visible_columns), callback=_on_dismiss)


def run_interactive(
    summaries: list[JobSummary],
    file_path: str | Path | None = None,
) -> None:
    """Launch the Textual TUI app."""
    app = ProwMonitorApp(summaries, file_path=file_path)
    app.run()
