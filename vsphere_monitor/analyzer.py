"""Filter vSphere periodic jobs and compute monitoring metrics."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class JobRun:
    """A single execution of a prow job."""

    job: str
    state: str
    start_time: datetime
    completion_time: datetime | None
    url: str
    build_id: str


@dataclass
class JobSummary:
    """Aggregated metrics for a unique job across all its runs."""

    job: str
    ocp_version: str
    job_variant: str  # e.g. "e2e-vsphere-ovn", "upgrade", "serial"
    runs: list[JobRun] = field(default_factory=list)

    @property
    def latest_run(self) -> JobRun:
        return self.runs[0]

    @property
    def latest_state(self) -> str:
        return self.latest_run.state

    @property
    def latest_url(self) -> str:
        return self.latest_run.url

    @property
    def total_runs(self) -> int:
        return len(self.runs)

    @property
    def failure_count(self) -> int:
        return sum(1 for r in self.runs if r.state == "failure")

    @property
    def failure_rate(self) -> float:
        if not self.runs:
            return 0.0
        return self.failure_count / self.total_runs

    @property
    def last_success(self) -> datetime | None:
        for r in self.runs:
            if r.state == "success":
                return r.start_time
        return None

    @property
    def last_success_age(self) -> str:
        ls = self.last_success
        if ls is None:
            return "never"
        delta = datetime.now(timezone.utc) - ls
        hours = delta.total_seconds() / 3600
        if hours < 1:
            return f"{int(delta.total_seconds() / 60)}m ago"
        if hours < 48:
            return f"{int(hours)}h ago"
        return f"{int(hours / 24)}d ago"

    @property
    def recent_states(self) -> list[str]:
        """Last N run states, most recent first."""
        return [r.state for r in self.runs[:6]]

    @property
    def state_sparkline(self) -> str:
        """Compact visual of recent states: S=success, F=failure, P=pending, etc."""
        mapping = {
            "success": "S",
            "failure": "F",
            "pending": "P",
            "aborted": "A",
            "error": "E",
            "triggered": "T",
        }
        return "".join(mapping.get(s, "?") for s in self.recent_states)


# Version extraction pattern: matches 4.XX or 5.X patterns
_VERSION_RE = re.compile(r"(?:^|-)(\d+\.\d+)(?:-|$)")


def _extract_ocp_version(job_name: str) -> str:
    """Extract the OCP version (e.g. '4.18') from a job name."""
    matches = _VERSION_RE.findall(job_name)
    if not matches:
        return "unknown"
    # For upgrade jobs like "upgrade-from-stable-4.19-e2e-...-4.20",
    # the target version is typically the last one
    return matches[-1]


def _extract_variant(job_name: str) -> str:
    """Extract a short variant description from the job name."""
    # Strip common prefixes
    name = job_name
    for prefix in ("periodic-ci-openshift-release-main-", "periodic-ci-openshift-",
                   "openshift-", "release-"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break

    # Identify key variants
    if "upgrade" in name:
        return "upgrade"
    if "serial" in name and "techpreview" in name:
        return "tp-serial"
    if "techpreview" in name:
        return "techpreview"
    if "serial" in name:
        return "serial"
    if "upi" in name:
        return "upi"
    if "static" in name:
        return "static"
    if "csi" in name:
        return "csi"
    if "zones" in name:
        return "zones"
    if "assisted" in name:
        return "assisted"
    if "operator" in name:
        return "operator"
    if "prfinder" in name:
        return "prfinder"
    return "e2e"


def _parse_time(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def extract_vsphere_periodic(raw: dict[str, Any]) -> list[JobRun]:
    """Filter raw prow data to only vSphere periodic job runs."""
    runs: list[JobRun] = []
    for item in raw.get("items", []):
        spec = item.get("spec", {})
        if spec.get("type") != "periodic":
            continue
        job_name = spec.get("job", "")
        if "vsphere" not in job_name.lower():
            continue

        status = item.get("status", {})
        start = _parse_time(status.get("startTime"))
        if start is None:
            continue

        runs.append(JobRun(
            job=job_name,
            state=status.get("state", "unknown"),
            start_time=start,
            completion_time=_parse_time(status.get("completionTime")),
            url=status.get("url", ""),
            build_id=status.get("build_id", ""),
        ))

    return runs


def aggregate(runs: list[JobRun]) -> list[JobSummary]:
    """Group runs by job name and compute per-job summaries."""
    by_job: dict[str, list[JobRun]] = {}
    for run in runs:
        by_job.setdefault(run.job, []).append(run)

    summaries: list[JobSummary] = []
    for job_name, job_runs in sorted(by_job.items()):
        # Sort runs most-recent-first
        job_runs.sort(key=lambda r: r.start_time, reverse=True)
        summaries.append(JobSummary(
            job=job_name,
            ocp_version=_extract_ocp_version(job_name),
            job_variant=_extract_variant(job_name),
            runs=job_runs,
        ))

    return summaries


def analyze(raw: dict[str, Any]) -> list[JobSummary]:
    """Full pipeline: filter -> aggregate -> return summaries."""
    runs = extract_vsphere_periodic(raw)
    return aggregate(runs)


def build_compact_summary(summaries: list[JobSummary]) -> str:
    """Build a token-minimal text summary for LLM consumption.

    This is the key to minimizing token spend: instead of sending raw JSON,
    we send a pre-computed compact representation.
    """
    lines: list[str] = []
    lines.append("VSPHERE PERIODIC JOB STATUS REPORT")
    lines.append(f"Jobs: {len(summaries)} | "
                 f"Failing: {sum(1 for s in summaries if s.latest_state == 'failure')} | "
                 f"Passing: {sum(1 for s in summaries if s.latest_state == 'success')} | "
                 f"Pending: {sum(1 for s in summaries if s.latest_state == 'pending')}")
    lines.append("")

    # Group by version
    by_version: dict[str, list[JobSummary]] = {}
    for s in summaries:
        by_version.setdefault(s.ocp_version, []).append(s)

    for version in sorted(by_version.keys(), key=lambda v: (v == "unknown", v)):
        jobs = by_version[version]
        failing = [j for j in jobs if j.latest_state == "failure"]
        lines.append(f"## OCP {version}: {len(jobs)} jobs, {len(failing)} failing")

        for j in jobs:
            fail_pct = f"{j.failure_rate:.0%}"
            lines.append(
                f"  {j.latest_state[0].upper()} {j.state_sparkline:<6} "
                f"fail={fail_pct:<4} last_ok={j.last_success_age:<8} "
                f"{j.job_variant:<12} {j.job}"
            )
        lines.append("")

    return "\n".join(lines)
