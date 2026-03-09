"""Detect install failure phase from Prow build logs."""

from __future__ import annotations

import asyncio
import re

import httpx
from rich.console import Console

from vsphere_monitor.analyzer import JobSummary
from vsphere_monitor.fetcher import (
    fetch_build_log_async,
    read_install_cache,
    write_install_cache,
)

# ---------------------------------------------------------------------------
# Install phase constants
# ---------------------------------------------------------------------------

PHASE_OK = "ok"
PHASE_INFRA = "infra"
PHASE_MANIFESTS = "manifests"
PHASE_IGNITION = "ignition"
PHASE_BOOTSTRAP = "bootstrap"
PHASE_INSTALL = "install"
PHASE_TERRAFORM = "terraform"
PHASE_UNKNOWN = "unknown"
PHASE_NOT_ANALYZED = "--"

# Maximum number of concurrent log fetches to avoid hammering GCS.
_MAX_CONCURRENCY = 10

# ---------------------------------------------------------------------------
# Detection regexes — compiled once at module level
# ---------------------------------------------------------------------------

# Infrastructure quota / throttling failures (exit code 4 or 5 from the
# installer scripts).  These take highest priority because they indicate
# environmental problems, not installer bugs.
_INFRA_RE = re.compile(
    r"Throttling:\s*Rate exceeded"
    r"|rateLimitExceeded"
    r"|The maximum number of [A-Za-z ]* has been reached"
    r"|The number of .* is larger than the maximum allowed size"
    r"|Quota .* exceeded"
    r"|Cannot create more than .* for this subscription"
    r"|The request is being throttled as the limit has been reached"
    r"|SkuNotAvailable"
    r"|Exceeded limit .* for zone"
    r"|ZONE_RESOURCE_POOL_EXHAUSTED"
    r"|Operation could not be completed as it results in exceeding approved .* quota"
    r"|A quota has been reached for project"
    r"|LimitExceeded.*exceed quota",
)

# IPI: manifest creation failure
_MANIFEST_FAIL_RE = re.compile(r"Create manifests exit code:\s*([1-9]\d*)")

# IPI: ignition config failure
_IGNITION_FAIL_RE = re.compile(r"Create ignition-configs exit code:\s*([1-9]\d*)")

# IPI (main + bastion scripts): installer exit with non-zero code
_INSTALLER_EXIT_RE = re.compile(r"Installer exit with code\s+(\d+)")

# IPI (AWS variant): different failure message
_AWS_INSTALL_FAIL_RE = re.compile(r"Installation failed \[create cluster\]")

# Phase markers — used to determine how far the install got
_BOOTSTRAP_MONITOR_RE = re.compile(r"Monitoring for bootstrap to complete")
_CLUSTER_MONITOR_RE = re.compile(r"Monitoring for cluster completion")

# UPI: terraform / powercli provisioning
_TERRAFORM_INIT_RE = re.compile(r"terraform init")
_TERRAFORM_APPLY_RE = re.compile(r"terraform apply")
_PWSH_UPI_RE = re.compile(r"pwsh upi\.ps1")

# Success indicators
_INSTALL_COMPLETE_RE = re.compile(r"Install complete!")


# ---------------------------------------------------------------------------
# Core detection function
# ---------------------------------------------------------------------------


def detect_install_phase(lines: list[str]) -> str:
    """Analyze build-log lines and return the install failure phase.

    Scans the log for known patterns from the IPI and UPI install scripts
    to determine which phase of installation failed.

    Returns one of the ``PHASE_*`` constants.
    """
    # Track which phase markers we've seen
    saw_bootstrap_monitor = False
    saw_cluster_monitor = False
    saw_terraform_init = False
    saw_terraform_apply = False
    saw_pwsh = False
    saw_infra = False
    installer_exit_code: int | None = None
    saw_manifest_fail = False
    saw_ignition_fail = False
    saw_aws_fail = False
    saw_install_complete = False

    for line in lines:
        # Phase markers
        if _BOOTSTRAP_MONITOR_RE.search(line):
            saw_bootstrap_monitor = True
        if _CLUSTER_MONITOR_RE.search(line):
            saw_cluster_monitor = True
        if _TERRAFORM_INIT_RE.search(line):
            saw_terraform_init = True
        if _TERRAFORM_APPLY_RE.search(line):
            saw_terraform_apply = True
        if _PWSH_UPI_RE.search(line):
            saw_pwsh = True

        # Infrastructure failures
        if _INFRA_RE.search(line):
            saw_infra = True

        # Manifest / ignition failures
        if _MANIFEST_FAIL_RE.search(line):
            saw_manifest_fail = True
        if _IGNITION_FAIL_RE.search(line):
            saw_ignition_fail = True

        # Installer exit code (take the last occurrence — retries append)
        m = _INSTALLER_EXIT_RE.search(line)
        if m:
            installer_exit_code = int(m.group(1))

        # AWS variant failure
        if _AWS_INSTALL_FAIL_RE.search(line):
            saw_aws_fail = True

        # Success indicator
        if _INSTALL_COMPLETE_RE.search(line):
            saw_install_complete = True

    # --- Priority-ordered classification ---

    # 1. Infrastructure failures (quota / throttling)
    if saw_infra:
        return PHASE_INFRA

    # 2. Manifest creation failure
    if saw_manifest_fail:
        return PHASE_MANIFESTS

    # 3. Ignition config failure
    if saw_ignition_fail:
        return PHASE_IGNITION

    # 4. IPI installer exit with non-zero code
    if installer_exit_code is not None and installer_exit_code != 0:
        # Did we get past bootstrap?
        if saw_cluster_monitor:
            return PHASE_INSTALL
        return PHASE_BOOTSTRAP

    # 5. AWS IPI variant failure
    if saw_aws_fail:
        return PHASE_INSTALL

    # 6. UPI terraform/powercli — if we started provisioning but never
    #    reached bootstrap monitoring, the provisioning step failed.
    if (saw_terraform_init or saw_terraform_apply or saw_pwsh) and not saw_bootstrap_monitor:
        return PHASE_TERRAFORM

    # 7. UPI — reached bootstrap monitoring but not cluster completion
    if saw_bootstrap_monitor and not saw_cluster_monitor:
        return PHASE_BOOTSTRAP

    # 8. UPI — reached cluster completion but no success indicator
    if saw_cluster_monitor and not saw_install_complete:
        return PHASE_INSTALL

    # 9. Success
    if saw_install_complete or installer_exit_code == 0:
        return PHASE_OK

    # 10. Couldn't classify
    return PHASE_UNKNOWN


# ---------------------------------------------------------------------------
# Async orchestration — fetch logs for failed jobs in parallel
# ---------------------------------------------------------------------------


async def _check_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    summary: JobSummary,
) -> tuple[str, str, str]:
    """Fetch log and detect install phase for a single failed job.

    Returns ``(job_name, build_id, phase)``.
    """
    build_id = summary.latest_run.build_id
    prow_url = summary.latest_url
    try:
        async with semaphore:
            log_lines = await fetch_build_log_async(prow_url, client)
        phase = detect_install_phase(log_lines)
    except Exception:
        phase = PHASE_UNKNOWN
    return summary.job, build_id, phase


async def _fetch_install_statuses_async(
    summaries: list[JobSummary],
    stderr: Console,
) -> dict[str, str]:
    """Fetch install status for all failed jobs, with caching.

    Returns a dict mapping job_name -> install phase string.
    Jobs whose latest state is not ``failure`` are mapped to
    ``PHASE_NOT_ANALYZED`` (for non-failure states) or ``PHASE_OK``
    (for success).
    """
    result: dict[str, str] = {}
    failed: list[JobSummary] = []

    for s in summaries:
        if s.latest_state == "success":
            result[s.job] = PHASE_OK
        elif s.latest_state == "failure":
            failed.append(s)
        else:
            result[s.job] = PHASE_NOT_ANALYZED

    if not failed:
        return result

    # Check cache — keyed by build_id so changing runs invalidate stale entries
    cache = read_install_cache()
    to_fetch: list[JobSummary] = []
    for s in failed:
        bid = s.latest_run.build_id
        if bid and bid in cache:
            result[s.job] = cache[bid]
        else:
            to_fetch.append(s)

    if not to_fetch:
        return result

    stderr.print(f"[dim]Fetching build logs for {len(to_fetch)} failed job(s)...[/]")

    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        tasks = [_check_one(client, semaphore, s) for s in to_fetch]
        outcomes = await asyncio.gather(*tasks)

    for job_name, build_id, phase in outcomes:
        result[job_name] = phase
        if build_id:
            cache[build_id] = phase

    write_install_cache(cache)
    return result


def fetch_install_statuses(
    summaries: list[JobSummary],
    stderr: Console | None = None,
) -> dict[str, str]:
    """Synchronous wrapper around the async install-status fetcher.

    Returns a dict mapping job_name -> install phase string.
    """
    if stderr is None:
        stderr = Console(stderr=True)
    return asyncio.run(_fetch_install_statuses_async(summaries, stderr))


async def fetch_install_statuses_async(
    summaries: list[JobSummary],
    stderr: Console | None = None,
) -> dict[str, str]:
    """Async version of install-status fetcher for use from async contexts (e.g. TUI reload).

    Returns a dict mapping job_name -> install phase string.
    """
    if stderr is None:
        stderr = Console(stderr=True)
    return await _fetch_install_statuses_async(summaries, stderr)
