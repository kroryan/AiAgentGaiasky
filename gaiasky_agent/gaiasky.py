"""
REST client for Gaia Sky's built-in APIv2 server.

Gaia Sky is never patched or modified: this module only ever speaks to it over
plain HTTP, exactly as any external script would. See PLAN.md section 4 for
the mechanics this client relies on (parameter matching, the "GUI not yet
initialized" state, the exception-swallowing quirk, and blocking sync calls).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests


class GaiaSkyError(Exception):
    """Raised when a call to Gaia Sky fails or cannot be interpreted."""


class GaiaSkyNotReady(GaiaSkyError):
    """Raised when the server is up but the GUI has not finished starting yet."""


def _fmt(value: Any) -> str:
    """Renders a Python value the way Gaia Sky's REST parameter parser expects it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        # repr() avoids Python's "1.0" -> "1" surprises for values that must stay doubles.
        return repr(value)
    return str(value)


def _unwrap(value: Any) -> Any:
    """
    Gaia Sky serializes return values with libgdx's Json writer. A boxed scalar
    (String, Double, Boolean, Long...) comes back as {"class": "...", "value": ...},
    because the writer has no static type to rely on for a bare Object; a primitive
    array (double[], String[]...) has no such ambiguity and comes back as a plain
    JSON array. This recursively strips the boxed-scalar envelope wherever it
    appears, so tools never have to special-case it.
    """
    if isinstance(value, dict):
        if set(value.keys()) == {"class", "value"}:
            return _unwrap(value["value"])
        return {k: _unwrap(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_unwrap(v) for v in value]
    return value


@dataclass
class GaiaSkyClient:
    base_url: str = "http://localhost:30007"
    timeout: float = 30.0

    def _endpoint(self, module_path: str, method: str) -> str:
        base = self.base_url.rstrip("/")
        if "://" not in base:
            base = "http://" + base
        return f"{base}/apiv2/{module_path}/{method}"

    def call(self, module_path: str, method: str, params: dict[str, Any] | None = None,
              timeout: float | None = None) -> Any:
        """
        Calls one APIv2 method and returns its "value". Raises GaiaSkyError on any
        failure Gaia Sky reports, and GaiaSkyNotReady while the GUI is still starting.

        Note: Gaia Sky itself swallows exceptions raised inside the invoked method
        and still answers with success=true, value=null (see PLAN.md quirk 1). Callers
        that need to be sure an action actually happened should verify its effect with
        a follow-up query, exactly as the original Java tools did.
        """
        query = {k: _fmt(v) for k, v in (params or {}).items() if v is not None}
        url = self._endpoint(module_path, method)
        try:
            response = requests.get(url, params=query, timeout=timeout or self.timeout)
        except requests.exceptions.ConnectionError as e:
            raise GaiaSkyError(
                f"Could not reach Gaia Sky at {self.base_url}. Is it running, and is "
                f"the REST API enabled (program → net → restPort in config.yaml)?"
            ) from e
        except requests.exceptions.Timeout as e:
            raise GaiaSkyError(
                f"Gaia Sky did not respond to '{method}' in time ({timeout or self.timeout}s)."
            ) from e

        try:
            data = response.json()
        except ValueError as e:
            raise GaiaSkyError(f"Gaia Sky sent a response that was not valid JSON: {response.text[:300]!r}") from e

        text = data.get("text", "")
        if not data.get("success", False):
            if "not yet initialized" in text.lower():
                raise GaiaSkyNotReady(text)
            raise GaiaSkyError(text or f"Gaia Sky rejected the call to '{module_path}/{method}'.")
        return _unwrap(data.get("value"))

    def ping(self) -> bool:
        """True if the server answers at all (regardless of GUI readiness)."""
        try:
            requests.get(self._endpoint("base", "help"), timeout=3)
            return True
        except requests.exceptions.RequestException:
            return False

    def wait_ready(self, timeout: float = 120.0, poll_interval: float = 1.0,
                   stop: "Any | None" = None) -> bool:
        """
        Blocks until Gaia Sky answers a harmless call successfully, meaning both the
        server is up and the GUI has finished starting. Returns False on timeout or
        if `stop` (an object with a truthy `.is_set()`/boolean check) is raised.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if stop is not None and (stop.is_set() if hasattr(stop, "is_set") else stop):
                return False
            try:
                self.call("base", "get_version")
                return True
            except GaiaSkyError:
                pass
            time.sleep(poll_interval)
        return False
