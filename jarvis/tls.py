"""jarvis/tls.py — self-signed certificate helpers for the Jarvis server.

The thin-client API carries a bearer token; serving it over plain HTTP on the
LAN would leak that secret to anything that can sniff the wire (Tailscale
encrypts *its* transit, but a direct LAN peer or a compromised box is not
covered). This module provides a dependency-free way to stand up a self-signed
TLS cert — via the ``openssl`` CLI, present on macOS and Git-for-Windows — so
the server can be served over HTTPS and the client can pin the cert's
fingerprint (real MitM resistance, no CA needed).

Config (all optional; TLS is OFF by default):
  JARVIS_TLS_CERT / JARVIS_TLS_KEY   paths to an existing PEM cert/key pair
  JARVIS_TLS_FINGERPRINT             sha256(hex) of the server cert to pin
"""
from __future__ import annotations

import hashlib
import os
import shutil
import ssl
import subprocess
from pathlib import Path

__all__ = ["cert_fingerprint", "configured_cert_key", "ensure_self_signed"]


def configured_cert_key() -> tuple[str | None, str | None]:
    """Return (cert, key) from the environment, validating they're paired."""
    cert = os.environ.get("JARVIS_TLS_CERT")
    key = os.environ.get("JARVIS_TLS_KEY")
    if bool(cert) != bool(key):
        raise ValueError("JARVIS_TLS_CERT and JARVIS_TLS_KEY must be set together")
    return cert, key


def ensure_self_signed(cert_path: Path, key_path: Path, cn: str = "jarvis") -> bool:
    """Generate a self-signed PEM cert + key with ``openssl`` if *cert_path* is
    missing. Never overwrites an existing pair. Returns True when a valid pair
    is present; raises RuntimeError if ``openssl`` is unavailable or fails.
    """
    if cert_path.exists() and key_path.exists():
        return True
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    exe = shutil.which("openssl")
    if not exe:
        raise RuntimeError(
            "openssl not found — install it or set JARVIS_TLS_CERT/JARVIS_TLS_KEY"
        )
    cmd = [exe, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
           "-days", "7300", "-subj", f"/CN={cn}",
           "-keyout", str(key_path), "-out", str(cert_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"openssl cert generation failed: {r.stderr.strip()}")
    return True


def cert_fingerprint(cert_path: Path) -> str:
    """SHA256 (hex) fingerprint of the cert's DER body — the value a client
    pins via JARVIS_TLS_FINGERPRINT to defeat active MitM without a CA."""
    der = ssl.PEM_cert_to_DER_cert(cert_path.read_bytes().decode("utf-8"))
    return hashlib.sha256(der).hexdigest()