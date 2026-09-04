"""Process-wide admission probe for sources required by a new research run."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

import requests

from data_sources.provider_request_policy import request as provider_request


_CHEMBL_STATUS_URL = "https://www.ebi.ac.uk/chembl/api/data/status.json"
_NCBI_EINFO_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi"
_SUCCESS_TTL_SECONDS = 30.0
_probe_lock = threading.Lock()
_cached_success: dict[str, Any] | None = None
_cached_success_until = 0.0


def _probe_json(
    provider: str,
    url: str,
    validator: Callable[[dict[str, Any]], bool],
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        response = provider_request(
            provider, requests.get, url, timeout=10, **kwargs)
        payload = response.json()
        if not isinstance(payload, dict) or not validator(payload):
            raise ValueError("empty or invalid status payload")
        return {"available": True, "error": None}
    except Exception as exc:
        return {
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def probe_required_sources() -> dict[str, Any]:
    """Probe ChEMBL and PubMed once each, caching only an all-healthy result.

    The lock coalesces simultaneous run admissions into one pair of probes.
    Failed results are returned immediately but never populate the success
    cache, so recovery can be observed by the very next admission attempt.
    """
    global _cached_success, _cached_success_until
    with _probe_lock:
        now = time.monotonic()
        if _cached_success is not None and now < _cached_success_until:
            return {
                "healthy": True,
                "cached": True,
                "sources": {
                    name: dict(status)
                    for name, status in _cached_success["sources"].items()
                },
            }

        sources = {
            "chembl": _probe_json(
                "chembl",
                _CHEMBL_STATUS_URL,
                lambda payload: str(payload.get("status") or "").upper()
                in {"UP", "OK", "HEALTHY"},
            ),
            "ncbi_pubmed": _probe_json(
                "ncbi",
                _NCBI_EINFO_URL,
                lambda payload: isinstance(payload.get("einforesult"), dict),
                params={"db": "pubmed", "retmode": "json"},
            ),
        }
        result = {
            "healthy": all(row["available"] for row in sources.values()),
            "cached": False,
            "sources": sources,
        }
        if result["healthy"]:
            _cached_success = result
            _cached_success_until = time.monotonic() + _SUCCESS_TTL_SECONDS
        return result


def _reset_for_tests() -> None:
    global _cached_success, _cached_success_until
    with _probe_lock:
        _cached_success = None
        _cached_success_until = 0.0