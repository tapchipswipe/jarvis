"""Tests for jarvis/tls.py and the thin-client HTTPS pinning path.

The pinning tests use a *real* local HTTPS server (http.server + ssl) so the
client's fingerprint check is exercised end-to-end — no CA, exactly like the
production self-signed setup. Skipped when the ``openssl`` CLI is unavailable
(it is on macOS + Git-for-Windows).
"""
from __future__ import annotations

import json
import shutil
import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from jarvis import remote, tls


def _openssl() -> bool:
    return shutil.which("openssl") is not None


# ── cert helpers ──────────────────────────────────────────────────────────────

@pytest.mark.skipif(not _openssl(), reason="openssl CLI required")
def test_ensure_self_signed_generates_idempotent_pair(tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    assert tls.ensure_self_signed(cert, key) is True
    assert cert.exists() and key.exists()
    before = cert.read_bytes()
    assert tls.ensure_self_signed(cert, key) is True  # never overwrites
    assert cert.read_bytes() == before
    fp = tls.cert_fingerprint(cert)
    assert len(fp) == 64 and all(c in "0123456789abcdef" for c in fp)


def test_configured_cert_key_requires_pair(monkeypatch):
    monkeypatch.setenv("JARVIS_TLS_CERT", "/tmp/c.pem")
    monkeypatch.setenv("JARVIS_TLS_KEY", "")
    with pytest.raises(ValueError):
        tls.configured_cert_key()
    monkeypatch.setenv("JARVIS_TLS_KEY", "/tmp/k.pem")
    assert tls.configured_cert_key() == ("/tmp/c.pem", "/tmp/k.pem")


def test_pinned_fingerprint_env_takes_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_TLS_FINGERPRINT", "ABCDEF")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert remote._pinned_fingerprint() == "abcdef"


def test_pinned_fingerprint_file_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_TLS_FINGERPRINT", "")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    cfg = tmp_path / ".config" / "jarvis"
    cfg.mkdir(parents=True)
    (cfg / "server-fingerprint").write_text("  ABCdef  \n", encoding="utf-8")
    assert remote._pinned_fingerprint() == "abcdef"


# ── end-to-end HTTPS pinning against a real local TLS server ─────────────────

class _Handler(BaseHTTPRequestHandler):
    headers_seen: ClassVar[list[dict]] = []  # captured per request for assertions

    def _respond(self):
        self.headers_seen.append(dict(self.headers))
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._respond()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self._respond()

    def log_message(self, *args):
        pass


@pytest.fixture()
def https_server(tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    tls.ensure_self_signed(cert, key)
    _Handler.headers_seen = []
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"port": port, "cert": cert}
    srv.shutdown()


def _https_client_env(monkeypatch, port):
    monkeypatch.setenv("JARVIS_MODE", "client")
    monkeypatch.setenv("JARVIS_REMOTE", f"https://127.0.0.1:{port}")


@pytest.mark.skipif(not _openssl(), reason="openssl CLI required")
def test_https_pinned_fingerprint_success(monkeypatch, https_server):
    _https_client_env(monkeypatch, https_server["port"])
    monkeypatch.setenv("JARVIS_TLS_FINGERPRINT",
                       tls.cert_fingerprint(https_server["cert"]))
    data = remote.health()
    assert data == {"ok": True}


@pytest.mark.skipif(not _openssl(), reason="openssl CLI required")
def test_https_pinned_fingerprint_mismatch_raises(monkeypatch, https_server):
    _https_client_env(monkeypatch, https_server["port"])
    monkeypatch.setenv("JARVIS_TLS_FINGERPRINT", "0" * 64)
    import urllib.error
    with pytest.raises(urllib.error.URLError) as ei:
        remote.health()
    assert isinstance(ei.value.reason, ssl.SSLError)
    assert "fingerprint mismatch" in str(ei.value.reason)


@pytest.mark.skipif(not _openssl(), reason="openssl CLI required")
def test_https_without_pin_works_unverified(monkeypatch, https_server):
    _https_client_env(monkeypatch, https_server["port"])
    monkeypatch.setenv("JARVIS_TLS_FINGERPRINT", "")
    assert remote.remote_ok() is True


@pytest.mark.skipif(not _openssl(), reason="openssl CLI required")
def test_remote_routes_over_https(monkeypatch, https_server):
    _https_client_env(monkeypatch, https_server["port"])
    monkeypatch.setenv("JARVIS_TLS_FINGERPRINT",
                       tls.cert_fingerprint(https_server["cert"]))
    dat = remote.ingest_status()
    assert dat == {"ok": True}


@pytest.mark.skipif(not _openssl(), reason="openssl CLI required")
def test_token_header_rides_over_https(monkeypatch, https_server):
    """The bearer token must be sent over the TLS channel (integration: the
    server-side guard reads X-Jarvis-Token, so it has to survive the wire)."""
    from jarvis import remote as _r

    _https_client_env(monkeypatch, https_server["port"])
    monkeypatch.setenv("JARVIS_TLS_FINGERPRINT",
                       tls.cert_fingerprint(https_server["cert"]))
    monkeypatch.setenv("JARVIS_TOKEN", "super-sekret")
    _r.remember_batch([{"content": "a memory over tls"}])
    assert _Handler.headers_seen
    seen = _Handler.headers_seen[-1]
    assert seen.get("X-Jarvis-Token") == "super-sekret"
