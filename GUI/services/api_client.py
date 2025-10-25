"""HTTP client for communicating with player REST APIs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from GUI.core.logger import get_logger


_LOG = get_logger(__name__)


@dataclass(slots=True)
class ApiClient:
    """Wrap player endpoints with convenient Python methods."""

    base_url: str
    api_key: str | None = None

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Issue an HTTP request and return a JSON payload if present."""
        url = f"{self.base_url}{path}"
        response = requests.request(
            method.upper(),
            url,
            json=json,
            params=params,
            timeout=timeout,
            headers=self._headers(),
        )
        response.raise_for_status()
        return self._decode_response(response)

    def get_status(self) -> dict[str, Any]:
        """Fetch player status information."""
        return self.request("get", "/status")

    def upload_media(self, media_url: str, target_path: str | None = None) -> dict[str, Any]:
        """Instruct the player to download media from a provided URL."""
        payload = {"url": media_url}
        if target_path:
            payload["filename"] = Path(target_path).name
        return self.request("post", "/download_asset", json=payload, timeout=120)

    def upload_media_push(self, file_path: Path, target_name: str | None = None) -> dict[str, Any]:
        """Upload a file directly to the player using multipart/form-data.

        This is a fallback when the player cannot reach our local file server.
        """
        url = f"{self.base_url}/upload_asset"
        name = Path(target_name or file_path.name).name
        params = {"filename": name}
        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        with open(file_path, "rb") as fh:
            files = {
                "file": (name, fh, "application/octet-stream"),
            }
            resp = requests.post(url, files=files, params=params, timeout=180, headers=headers)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {"ok": True, "status_code": resp.status_code}

    def update_player(self, version: str, bundle_url: str, checksum: str | None = None) -> dict[str, Any]:
        """Kick off a two-phase update process on the player."""
        payload: dict[str, Any] = {"version": version, "url": bundle_url}
        if checksum:
            payload["sha256"] = checksum
        return self.request("post", "/download_update", json=payload, timeout=30)

    def get_autoplay(self) -> dict[str, Any]:
        """Read current autoplay state."""
        return self.request("get", "/autoplay")

    def set_autoplay(
        self,
        *,
        enabled: bool,
        restart: bool = True,
        delay: float | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Toggle autoplay on the player."""
        payload: dict[str, Any] = {"enabled": bool(enabled), "restart": bool(restart)}
        if delay is not None:
            payload["delay"] = float(delay)
        return self.request("post", "/autoplay", json=payload, timeout=timeout)

    @staticmethod
    def _decode_response(response: requests.Response) -> dict[str, Any]:
        """Return JSON content if available, otherwise a basic success payload."""
        if not response.content:
            return {"ok": True, "status_code": response.status_code}
        try:
            return response.json()
        except ValueError:
            return {"ok": True, "status_code": response.status_code, "text": response.text}
