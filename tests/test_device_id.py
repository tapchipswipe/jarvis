"""Tests for the persisted device-id used by collectors/ingest (the `device` tag)."""
from __future__ import annotations

import hashlib

from jarvis import device_id


def test_get_device_id_creates_and_is_deterministic(tmp_path, monkeypatch):
    f = tmp_path / "device-id"
    monkeypatch.setattr(device_id, "DEVICE_ID_FILE", f)
    monkeypatch.setattr(device_id, "_get_hostname", lambda: "myhost")
    monkeypatch.setattr(device_id, "_mac_address", lambda: "aa:bb:cc:dd:ee:ff")

    expected = hashlib.sha256(b"myhost:aa:bb:cc:dd:ee:ff").hexdigest()[:12]
    a = device_id.get_device_id()
    assert a == expected
    assert f.exists() and f.read_text().strip() == a

    b = device_id.get_device_id()
    assert b == a  # reads back the persisted value; no churn


def test_get_device_id_reads_existing_file(tmp_path, monkeypatch):
    f = tmp_path / "device-id"
    f.write_text("abc123def456", encoding="utf-8")
    monkeypatch.setattr(device_id, "DEVICE_ID_FILE", f)
    assert device_id.get_device_id() == "abc123def456"
