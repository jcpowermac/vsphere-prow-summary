"""Fetch Prow job data from the API or local file, with 30-minute caching."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

PROW_API_URL = (
    "https://prow.ci.openshift.org/prowjobs.js"
    "?omit=annotations,decoration_config,pod_spec"
)
CACHE_DIR = Path.home() / ".cache" / "vsphere-prow-monitor"
CACHE_TTL_SECONDS = 30 * 60  # 30 minutes


def _cache_path(url: str) -> Path:
    """Return a cache file path based on a hash of the URL."""
    h = hashlib.sha256(url.encode()).hexdigest()[:16]
    return CACHE_DIR / f"prowjobs_{h}.json"


def _cache_meta_path(url: str) -> Path:
    return _cache_path(url).with_suffix(".meta")


def _is_cache_valid(url: str) -> bool:
    meta = _cache_meta_path(url)
    if not meta.exists():
        return False
    try:
        ts = float(meta.read_text().strip())
        return (time.time() - ts) < CACHE_TTL_SECONDS
    except (ValueError, OSError):
        return False


def _write_cache(url: str, data: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = _cache_path(url)
    cache.write_text(json.dumps(data))
    _cache_meta_path(url).write_text(str(time.time()))


def _read_cache(url: str) -> dict[str, Any]:
    return json.loads(_cache_path(url).read_text())


def fetch_from_api(*, refresh: bool = False) -> dict[str, Any]:
    """Fetch prow jobs from the live API, using cache unless refresh=True."""
    if not refresh and _is_cache_valid(PROW_API_URL):
        return _read_cache(PROW_API_URL)

    with httpx.Client(timeout=120, follow_redirects=True) as client:
        resp = client.get(PROW_API_URL)
        resp.raise_for_status()
        data = resp.json()

    _write_cache(PROW_API_URL, data)
    return data


def fetch_from_file(path: str | Path) -> dict[str, Any]:
    """Load prow jobs from a local JSON file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    with p.open() as f:
        return json.load(f)


def fetch(file: str | Path | None = None, *, refresh: bool = False) -> dict[str, Any]:
    """Unified fetch: use local file if provided, otherwise hit the API."""
    if file is not None:
        return fetch_from_file(file)
    return fetch_from_api(refresh=refresh)
