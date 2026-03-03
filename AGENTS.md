# AGENTS.md — Coding Agent Guidelines

## Project Overview

vsphere-prow-monitor is a Python CLI/TUI tool that monitors OpenShift vSphere periodic Prow CI
jobs. It fetches data from the Prow API, computes failure metrics, and presents results as Rich
tables, JSON, an interactive Textual TUI, or via natural language queries (Claude/Anthropic).

## Architecture

```
vsphere_monitor/
├── cli.py         # Argparse CLI entry point (vsphere-monitor command)
├── fetcher.py     # HTTP fetching with 30-min disk cache, build log streaming
├── analyzer.py    # Domain models (JobRun, JobSummary dataclasses), metrics, filtering
├── formatters.py  # Rich table and JSON output formatting
├── tui.py         # Textual interactive TUI with DataTable, log viewer, column toggles
├── llm.py         # Claude (Anthropic) integration for natural language queries
├── __main__.py    # python -m vsphere_monitor entry point
└── __init__.py
tests/             # pytest test directory
```

## Setup & Dependencies

This project uses **uv** for dependency management. Python >= 3.11 is required.

```bash
# Install all dependencies (runtime + dev)
uv sync --dev

# Install runtime dependencies only
uv sync
```

## Build / Run Commands

```bash
# Run the CLI
uv run vsphere-monitor --help
uv run vsphere-monitor                          # default table output
uv run vsphere-monitor --format json             # JSON output
uv run vsphere-monitor -i                        # interactive TUI
uv run vsphere-monitor --ask "which jobs are failing?"  # LLM query (needs ANTHROPIC_API_KEY)
uv run vsphere-monitor --summary                 # compact text for piping to LLM

# Run as module
uv run python -m vsphere_monitor
```

## Lint & Format

Ruff is used for both linting and formatting. Configuration is in `pyproject.toml`.

```bash
# Lint (check only)
uv run ruff check vsphere_monitor/ tests/

# Lint with auto-fix
uv run ruff check --fix vsphere_monitor/ tests/

# Format (check only)
uv run ruff format --check vsphere_monitor/ tests/

# Format (apply)
uv run ruff format vsphere_monitor/ tests/

# Type checking
uv run mypy vsphere_monitor/
```

### Ruff Rules

Configured in `pyproject.toml` under `[tool.ruff]`:
- Line length: 99
- Target: Python 3.11
- Enabled rule sets: `E` (pycodestyle), `F` (pyflakes), `I` (isort), `UP` (pyupgrade),
  `B` (bugbear), `SIM` (simplify)
- isort knows `vsphere_monitor` as first-party

## Testing

Tests use **pytest** and live in the `tests/` directory.

```bash
# Run all tests
uv run pytest

# Run all tests with verbose output
uv run pytest -v

# Run a single test file
uv run pytest tests/test_analyzer.py

# Run a single test function
uv run pytest tests/test_analyzer.py::test_extract_ocp_version

# Run tests matching a keyword
uv run pytest -k "failure_rate"

# Run with coverage (if pytest-cov is installed)
uv run pytest --cov=vsphere_monitor
```

## Code Style Guidelines

### Imports

- Every module starts with `from __future__ import annotations` as the first import.
- Import order (enforced by ruff isort):
  1. `__future__`
  2. Standard library (`import re`, `from pathlib import Path`)
  3. Third-party (`import httpx`, `from rich.console import Console`)
  4. First-party (`from vsphere_monitor.analyzer import JobSummary`)
- Blank line between each group.
- Prefer `from X import Y` for specific items over bare `import X` when using few names.

### Formatting

- **Line length**: 99 characters max.
- **Indentation**: 4 spaces (no tabs).
- **Quotes**: Double quotes for strings.
- **Trailing commas**: Use trailing commas in multi-line collections and function args.
- **Blank lines**: Two blank lines before top-level functions/classes, one blank line between
  methods.

### Type Annotations

- All function signatures must have type annotations for parameters and return types.
- Use modern Python syntax (PEP 604 unions): `str | None` not `Optional[str]`.
- Use lowercase generics: `list[str]`, `dict[str, Any]`, `tuple[str, list[str]]`.
- For complex types, import from `typing` only what's needed (e.g., `Any`).
- `from __future__ import annotations` enables all modern annotation syntax on Python 3.11+.

### Naming Conventions

- **Modules**: `snake_case.py`
- **Classes**: `PascalCase` (e.g., `JobSummary`, `ProwMonitorApp`, `LogViewerScreen`)
- **Functions/methods**: `snake_case` (e.g., `fetch_build_log`, `build_compact_summary`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `PROW_API_URL`, `CACHE_TTL_SECONDS`)
- **Private**: Prefix with underscore (`_build_parser`, `_STATE_STYLE`, `_cache_path`)
- **Module-level compiled regexes**: `_UPPER_SNAKE_RE` (e.g., `_VERSION_RE`, `_ERROR_RE`)

### Dataclasses

- Use `@dataclass` for domain models (see `JobRun`, `JobSummary` in `analyzer.py`).
- Prefer `@property` for computed/derived values over storing redundant state.
- Use `field(default_factory=list)` for mutable defaults.

### Error Handling

- Use specific exception types: `FileNotFoundError`, `ValueError`, `httpx.HTTPStatusError`.
- Document raised exceptions in docstrings.
- In CLI code, catch broad `Exception` at the top level and print user-friendly messages via
  `console.print(f"[red bold]Error:[/] {e}")` then `sys.exit(1)`.
- For missing optional features (e.g., API key not set), return a descriptive error string
  rather than raising.

### Docstrings

- Module-level docstrings: one-line description at the top of every file.
- Function/class docstrings: imperative mood, concise. Multi-line for complex functions.
- Use `"""triple double quotes"""`.

### HTTP Clients

- Use `httpx` (not `requests`). Always use context managers: `with httpx.Client(...) as client:`.
- Set explicit timeouts (120s for Prow API calls).
- Use `follow_redirects=True`.

### Textual TUI Patterns

- CSS styles go in class-level `CSS` string constants, not external files.
- Use `Binding` for keyboard shortcuts with descriptive labels.
- Use `run_worker()` for async I/O operations.
- Handle worker results via `on_worker_state_changed`.

### Output

- Rich `Console(stderr=True)` for status/progress output (keeps stdout clean for data).
- `Console()` (stdout) for actual output content.
- Use Rich markup for colored output: `[red bold]Error:[/]`, `[dim]info[/]`.
