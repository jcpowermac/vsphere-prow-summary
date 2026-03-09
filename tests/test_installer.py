"""Tests for install phase detection from build logs."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from vsphere_monitor.installer import (
    PHASE_BOOTSTRAP,
    PHASE_IGNITION,
    PHASE_INFRA,
    PHASE_INSTALL,
    PHASE_MANIFESTS,
    PHASE_OK,
    PHASE_TERRAFORM,
    PHASE_UNKNOWN,
    detect_install_phase,
)

# ---------------------------------------------------------------------------
# detect_install_phase — IPI success
# ---------------------------------------------------------------------------


class TestDetectInstallPhaseSuccess:
    """Tests for successful install detection."""

    def test_ipi_success_exit_code_zero(self) -> None:
        lines = [
            "Installing from release registry.ci.openshift.org/...",
            "Installer exit with code 0",
        ]
        assert detect_install_phase(lines) == PHASE_OK

    def test_ipi_success_install_complete(self) -> None:
        lines = [
            "Monitoring for bootstrap to complete",
            "Monitoring for cluster completion...",
            'level=info msg="Install complete!"',
        ]
        assert detect_install_phase(lines) == PHASE_OK

    def test_upi_success_install_complete(self) -> None:
        lines = [
            "terraform init...",
            "terraform apply...",
            "Monitoring for bootstrap to complete",
            "Monitoring for cluster completion...",
            'level=info msg="Install complete!"',
            "Configuring image registry with emptyDir...",
        ]
        assert detect_install_phase(lines) == PHASE_OK


# ---------------------------------------------------------------------------
# detect_install_phase — infrastructure failures
# ---------------------------------------------------------------------------


class TestDetectInstallPhaseInfra:
    """Tests for infrastructure quota/throttling failures."""

    def test_throttling_rate_exceeded(self) -> None:
        lines = [
            "Installer exit with code 4",
            "Throttling: Rate exceeded",
        ]
        assert detect_install_phase(lines) == PHASE_INFRA

    def test_quota_exceeded(self) -> None:
        lines = [
            "Installer exit with code 5",
            "Quota 'CPUS' exceeded. Limit: 100",
        ]
        assert detect_install_phase(lines) == PHASE_INFRA

    def test_zone_resource_pool_exhausted(self) -> None:
        lines = [
            "ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS",
            "Installer exit with code 4",
        ]
        assert detect_install_phase(lines) == PHASE_INFRA

    def test_sku_not_available(self) -> None:
        lines = ["SkuNotAvailable for the requested VM size"]
        assert detect_install_phase(lines) == PHASE_INFRA

    def test_rate_limit_exceeded(self) -> None:
        lines = ["rateLimitExceeded for API calls"]
        assert detect_install_phase(lines) == PHASE_INFRA

    def test_subscription_limit(self) -> None:
        lines = ["Cannot create more than 10 VMs for this subscription"]
        assert detect_install_phase(lines) == PHASE_INFRA

    def test_project_quota(self) -> None:
        lines = ["A quota has been reached for project my-project"]
        assert detect_install_phase(lines) == PHASE_INFRA

    def test_limit_exceeded_quota(self) -> None:
        lines = ["LimitExceeded: You have exceed quota for resources"]
        assert detect_install_phase(lines) == PHASE_INFRA

    def test_infra_takes_priority_over_exit_code(self) -> None:
        """Infrastructure failure should be reported even with a bootstrap failure."""
        lines = [
            "Monitoring for bootstrap to complete",
            "Throttling: Rate exceeded",
            "Installer exit with code 4",
        ]
        assert detect_install_phase(lines) == PHASE_INFRA


# ---------------------------------------------------------------------------
# detect_install_phase — manifest failures
# ---------------------------------------------------------------------------


class TestDetectInstallPhaseManifests:
    """Tests for manifest creation failures."""

    def test_manifest_failure(self) -> None:
        lines = [
            "Installing from release ...",
            "Create manifests exit code: 1",
        ]
        assert detect_install_phase(lines) == PHASE_MANIFESTS

    def test_manifest_failure_exit_code_2(self) -> None:
        lines = ["Create manifests exit code: 2"]
        assert detect_install_phase(lines) == PHASE_MANIFESTS


# ---------------------------------------------------------------------------
# detect_install_phase — ignition failures
# ---------------------------------------------------------------------------


class TestDetectInstallPhaseIgnition:
    """Tests for ignition config failures."""

    def test_ignition_failure(self) -> None:
        lines = [
            "Create manifests exit code: 0",
            "Create ignition-configs exit code: 1",
        ]
        assert detect_install_phase(lines) == PHASE_IGNITION


# ---------------------------------------------------------------------------
# detect_install_phase — bootstrap failures
# ---------------------------------------------------------------------------


class TestDetectInstallPhaseBootstrap:
    """Tests for bootstrap phase failures."""

    def test_ipi_bootstrap_failure_exit_code(self) -> None:
        """IPI: installer exits non-zero and never reached cluster monitoring."""
        lines = [
            "Monitoring for bootstrap to complete",
            'level=error msg="bootstrap failed"',
            "Installer exit with code 1",
        ]
        assert detect_install_phase(lines) == PHASE_BOOTSTRAP

    def test_ipi_bootstrap_failure_no_cluster_monitor(self) -> None:
        """IPI: exit code non-zero, no 'Monitoring for cluster completion' seen."""
        lines = [
            "Installing from release ...",
            "Installer exit with code 1",
        ]
        assert detect_install_phase(lines) == PHASE_BOOTSTRAP

    def test_upi_bootstrap_failure(self) -> None:
        """UPI: reached bootstrap monitoring but not cluster completion."""
        lines = [
            "terraform init...",
            "terraform apply...",
            "Monitoring for bootstrap to complete",
            'level=error msg="bootstrap failed"',
        ]
        assert detect_install_phase(lines) == PHASE_BOOTSTRAP


# ---------------------------------------------------------------------------
# detect_install_phase — install-complete failures
# ---------------------------------------------------------------------------


class TestDetectInstallPhaseInstall:
    """Tests for install-complete phase failures."""

    def test_ipi_install_failure_with_cluster_monitor(self) -> None:
        """IPI: installer exits non-zero after reaching cluster monitoring."""
        lines = [
            "Monitoring for bootstrap to complete",
            "Monitoring for cluster completion...",
            'level=error msg="cluster operator degraded"',
            "Installer exit with code 1",
        ]
        assert detect_install_phase(lines) == PHASE_INSTALL

    def test_aws_install_failure(self) -> None:
        """AWS IPI variant: uses different failure message."""
        lines = [
            "Installation failed [create cluster]",
        ]
        assert detect_install_phase(lines) == PHASE_INSTALL

    def test_upi_install_failure(self) -> None:
        """UPI: reached cluster completion but no Install complete."""
        lines = [
            "terraform init...",
            "terraform apply...",
            "Monitoring for bootstrap to complete",
            "Monitoring for cluster completion...",
            'level=error msg="cluster operator timeout"',
        ]
        assert detect_install_phase(lines) == PHASE_INSTALL


# ---------------------------------------------------------------------------
# detect_install_phase — terraform failures
# ---------------------------------------------------------------------------


class TestDetectInstallPhaseTerraform:
    """Tests for terraform/powercli provisioning failures."""

    def test_terraform_init_failure(self) -> None:
        """UPI: terraform init started but bootstrap monitoring never reached."""
        lines = [
            "terraform init...",
            "Error: Failed to initialize provider",
        ]
        assert detect_install_phase(lines) == PHASE_TERRAFORM

    def test_terraform_apply_failure(self) -> None:
        """UPI: terraform apply started but bootstrap monitoring never reached."""
        lines = [
            "terraform init...",
            "terraform apply...",
            "Error: creating vSphere virtual machine",
        ]
        assert detect_install_phase(lines) == PHASE_TERRAFORM

    def test_pwsh_failure(self) -> None:
        """UPI: pwsh upi.ps1 started but bootstrap monitoring never reached."""
        lines = [
            "pwsh upi.ps1...",
            "Error: PowerCLI failed",
        ]
        assert detect_install_phase(lines) == PHASE_TERRAFORM


# ---------------------------------------------------------------------------
# detect_install_phase — unknown / edge cases
# ---------------------------------------------------------------------------


class TestDetectInstallPhaseUnknown:
    """Tests for unclassifiable failures and edge cases."""

    def test_empty_log(self) -> None:
        assert detect_install_phase([]) == PHASE_UNKNOWN

    def test_no_recognizable_patterns(self) -> None:
        lines = [
            "some random log output",
            "nothing recognizable here",
        ]
        assert detect_install_phase(lines) == PHASE_UNKNOWN

    def test_only_error_lines_no_phase_markers(self) -> None:
        lines = [
            'level=error msg="something bad happened"',
            "exit code 1",
        ]
        assert detect_install_phase(lines) == PHASE_UNKNOWN


# ---------------------------------------------------------------------------
# detect_install_phase — retry scenarios
# ---------------------------------------------------------------------------


class TestDetectInstallPhaseRetries:
    """Tests for IPI retry scenarios (multiple install attempts)."""

    def test_retry_final_success(self) -> None:
        """First attempt fails, retry succeeds — should report ok."""
        lines = [
            "Install attempt 1 of 3",
            "Installer exit with code 1",
            "Install attempt 2 of 3",
            "Installer exit with code 0",
        ]
        assert detect_install_phase(lines) == PHASE_OK

    def test_retry_all_fail(self) -> None:
        """All attempts fail — should report the failure phase."""
        lines = [
            "Install attempt 1 of 3",
            "Installer exit with code 1",
            "Install attempt 2 of 3",
            "Installer exit with code 1",
        ]
        assert detect_install_phase(lines) == PHASE_BOOTSTRAP


# ---------------------------------------------------------------------------
# Install cache round-trip
# ---------------------------------------------------------------------------


class TestInstallCache:
    """Tests for install status cache read/write."""

    def test_cache_round_trip(self, tmp_path: Path) -> None:
        cache_file = tmp_path / "install_status.json"
        data = {"build123": "bootstrap", "build456": "ok"}

        with patch("vsphere_monitor.fetcher._INSTALL_CACHE_FILE", cache_file):
            from vsphere_monitor.fetcher import read_install_cache, write_install_cache

            write_install_cache(data)
            assert cache_file.exists()
            loaded = read_install_cache()
            assert loaded == data

    def test_cache_read_missing_file(self, tmp_path: Path) -> None:
        cache_file = tmp_path / "nonexistent.json"

        with patch("vsphere_monitor.fetcher._INSTALL_CACHE_FILE", cache_file):
            from vsphere_monitor.fetcher import read_install_cache

            assert read_install_cache() == {}

    def test_cache_read_corrupt_file(self, tmp_path: Path) -> None:
        cache_file = tmp_path / "corrupt.json"
        cache_file.write_text("not valid json{{{")

        with patch("vsphere_monitor.fetcher._INSTALL_CACHE_FILE", cache_file):
            from vsphere_monitor.fetcher import read_install_cache

            assert read_install_cache() == {}
