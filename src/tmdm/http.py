"""HTTP helpers with retries, polite pacing and consistent headers."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from tmdm.config import USER_AGENT

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


def session() -> requests.Session:
    """Return a requests session with the project user agent."""
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    return s


def get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 120,
    retries: int = 5,
    backoff: float = 2.0,
    sess: requests.Session | None = None,
) -> requests.Response:
    """GET a URL with exponential backoff on transient failures.

    Raises the last error if every attempt fails.
    """
    s = sess or session()
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            r = s.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code in RETRY_STATUSES:
                wait = backoff * (2**attempt)
                retry_after = r.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    wait = max(wait, float(retry_after))
                log.warning("HTTP %s for %s, retrying in %.0fs", r.status_code, r.url, wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            wait = backoff * (2**attempt)
            log.warning("%s for %s, retrying in %.0fs", type(exc).__name__, url, wait)
            time.sleep(wait)
    raise RuntimeError(f"GET failed after {retries} attempts: {url}") from last_exc


def post_json(
    url: str,
    body: Any,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 180,
    retries: int = 4,
    backoff: float = 3.0,
    sess: requests.Session | None = None,
) -> requests.Response:
    """POST a JSON body with exponential backoff on transient failures."""
    s = sess or session()
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            r = s.post(url, json=body, params=params, timeout=timeout)
            if r.status_code in RETRY_STATUSES:
                wait = backoff * (2**attempt)
                log.warning("HTTP %s for %s, retrying in %.0fs", r.status_code, r.url, wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            time.sleep(backoff * (2**attempt))
    raise RuntimeError(f"POST failed after {retries} attempts: {url}") from last_exc
