"""Textual TUI for interactive vSphere Prow job monitoring."""

from __future__ import annotations

import webbrowser

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Static

from vsphere_monitor.analyzer import JobSummary
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


def _sparkline_plain(summary: JobSummary) -> str:
    mapping = {"success": "S", "failure": "F", "pending": "P",
               "aborted": "A", "error": "E", "triggered": "T"}
    return "".join(mapping.get(s, "?") for s in summary.recent_states)


class StatsBar(Static):
    """Displays aggregate stats and active filters."""

    def __init__(self, summaries: list[JobSummary]) -> None:
        super().__init__()
        self._all = summaries
        self._version_filter: str | None = None
        self._state_filter: str | None = None
        self._sort_by: str = "recent"

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
    """

    BINDINGS = [
        Binding("v", "cycle_version", "Cycle version filter"),
        Binding("s", "cycle_state", "Cycle state filter"),
        Binding("o", "cycle_sort", "Cycle sort order"),
        Binding("c", "clear_filters", "Clear filters"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, summaries: list[JobSummary]) -> None:
        super().__init__()
        self._all_summaries = summaries
        self._versions = [None] + sorted(
            set(s.ocp_version for s in summaries),
            key=lambda v: (v == "unknown", v),
        )
        self._version_idx = 0
        self._state_idx = 0
        self._sort_idx = 0
        # Ordered list of summaries currently displayed, for URL lookup
        self._displayed: list[JobSummary] = []

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

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_idx = event.cursor_row
        if 0 <= row_idx < len(self._displayed):
            url = self._displayed[row_idx].latest_url
            if url:
                webbrowser.open(url)

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


def run_interactive(summaries: list[JobSummary]) -> None:
    """Launch the Textual TUI app."""
    app = ProwMonitorApp(summaries)
    app.run()
