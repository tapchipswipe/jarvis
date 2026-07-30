import json
from typing import Any

import requests


class MonitorClientError(Exception):
    pass


class MonitorClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8765"):
        self.base_url = base_url.rstrip("/")

    def _get(self, path: str) -> Any:
        try:
            resp = requests.get(f"{self.base_url}{path}", timeout=5)
        except requests.RequestException as e:
            raise MonitorClientError(str(e)) from e
        if resp.status_code != 200:
            raise MonitorClientError(f"HTTP {resp.status_code}: {resp.text}")
        try:
            return resp.json()
        except ValueError as e:
            raise MonitorClientError(f"Invalid JSON: {e}") from e

    def get_status(self) -> dict:
        return self._get("/status")

    def get_devices(self) -> dict:
        return self._get("/devices")

    def get_queue(self) -> dict:
        return self._get("/queue")

    def get_conflicts(self) -> list:
        return self._get("/conflicts")
