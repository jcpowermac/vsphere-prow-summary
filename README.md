# vsphere-prow-monitor

Monitor OpenShift vSphere periodic Prow CI jobs. Fetches job data from the
[Prow API](https://prow.ci.openshift.org/), computes failure metrics, and
presents results as Rich tables, JSON, an interactive TUI, or via natural
language queries powered by Claude.

## Quick Start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
# Install dependencies
uv sync

# Run the monitor (default: table output)
uv run vsphere-monitor
```

## Usage

```
vsphere-monitor [OPTIONS]
```

### Output Modes

| Flag | Description |
|------|-------------|
| *(default)* | Rich table printed to stdout |
| `--format json` | Machine-readable JSON output |
| `-i` / `--interactive` | Launch the Textual TUI |
| `--ask "question"` | Ask Claude about job status (requires `ANTHROPIC_API_KEY`) |
| `--summary` | Compact text summary, useful for piping to an LLM |

### Filters & Sorting

| Flag | Description |
|------|-------------|
| `-v VER` / `--version VER` | Filter by OCP version (e.g. `4.18`) |
| `-s STATE` / `--state STATE` | Filter by state (`success`, `failure`, `pending`, `aborted`, `error`) |
| `--sort KEY` | Sort by `recent` (default), `version`, `failure_rate`, or `state` |

### Data Source

| Flag | Description |
|------|-------------|
| `-f PATH` / `--file PATH` | Read from a local `prowjobs.json` instead of the API |
| `--refresh` | Bypass the 30-minute disk cache and fetch fresh data |

### Examples

```bash
# Show only failing jobs for OCP 4.18, sorted by failure rate
uv run vsphere-monitor -v 4.18 -s failure --sort failure_rate

# Export all job data as JSON
uv run vsphere-monitor --format json > jobs.json

# Ask a question using Claude
export ANTHROPIC_API_KEY=sk-ant-...
uv run vsphere-monitor --ask "which jobs have been failing the most?"

# Interactive TUI
uv run vsphere-monitor -i
```

### TUI Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `r` | Reload data |
| `v` | Cycle version filter |
| `s` | Cycle state filter |
| `o` | Cycle sort order |
| `c` | Clear all filters |
| `w` | Open selected job in browser |
| `h` | Toggle column visibility |
| `Enter` | View build log for selected job |
| `q` / `Esc` | Quit |

## Development

```bash
# Install with dev dependencies
uv sync --dev

# Lint
uv run ruff check vsphere_monitor/ tests/

# Format
uv run ruff format vsphere_monitor/ tests/

# Type check
uv run mypy vsphere_monitor/

# Run tests
uv run pytest -v
```

See [AGENTS.md](AGENTS.md) for detailed code style guidelines and conventions.

## Architecture

| Module | Responsibility |
|--------|---------------|
| `cli.py` | Argparse CLI entry point |
| `fetcher.py` | HTTP fetching with SHA256-keyed 30-min disk cache |
| `analyzer.py` | Domain models (`JobRun`, `JobSummary`), metrics, filtering |
| `formatters.py` | Rich table and JSON output |
| `tui.py` | Textual interactive TUI with DataTable and log viewer |
| `llm.py` | Claude integration for natural language queries |
