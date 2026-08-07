"""
jarvis/paths.py — Central path resolution for all Jarvis storage.

Multi-user isolation
--------------------
Every storage / config location resolves through the helpers here, so a second
profile on the same machine is created simply by pointing the environment at a
different directory (or username) — no code changes needed:

    JARVIS_DATA_DIR=/path/to/data   jarvis status      # explicit data root
    JARVIS_USER=alice               jarvis status      # ~/jarvis/users/alice
    JARVIS_CONFIG_DIR=/path/cfg     jarvis status      # explicit config root

Precedence
----------
Data root:   JARVIS_DATA_DIR  >  ~/jarvis/users/<user> (if user != current OS
             user)  >  ~/jarvis
Config dir:  JARVIS_CONFIG_DIR  >  data_root()/config  (if per-user)  >  ~/.config/jarvis

Access control: created directories are chmod'd 0700 (owner-only) so other OS
users can't read another profile's data.
"""
from __future__ import annotations

import getpass
import os
from pathlib import Path


def user_name() -> str:
    """Active profile name (JARVIS_USER or the current OS username)."""
    return os.environ.get("JARVIS_USER") or _os_username()


def _os_username() -> str:
    try:
        return getpass.getuser()
    except (KeyError, OSError, ImportError):
        return os.environ.get("USER", "user")


def data_root() -> Path:
    """Root directory for all Jarvis data (DBs, chroma, exports, logs)."""
    env = os.environ.get("JARVIS_DATA_DIR")
    if env:
        return Path(env).expanduser()
    user = user_name()
    if user and user != _os_username():
        # Secondary profile: isolate under ~/jarvis/users/<user>
        return Path.home() / "jarvis" / "users" / user
    return Path.home() / "jarvis"


def config_dir() -> Path:
    """Root directory for Jarvis config (task queue, triggers, device-id)."""
    env = os.environ.get("JARVIS_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    user = user_name()
    if user and user != _os_username():
        # Keep config beside the per-user data for full isolation.
        return data_root() / "config"
    return Path.home() / ".config" / "jarvis"


def data_dir(*parts: str) -> Path:
    """Join *parts* under the data root (data_root()/data/...)."""
    if parts:
        return data_root().joinpath(*parts)
    return data_root()


def config_file(*parts: str) -> Path:
    """Join *parts* under the config dir."""
    return config_dir().joinpath(*parts)


def logs_dir(*parts: str) -> Path:
    """Join *parts* under the logs dir (data_root()/logs)."""
    if parts:
        return data_root().joinpath("logs", *parts)
    return data_root() / "logs"


def ensure_private_dir(path: Path) -> Path:
    """Create a directory (parents too) with owner-only permissions.

    Every directory that Jarvis actually *creates* — including intermediate
    parents that don't exist yet — is chmod'd 0700, so a secondary profile's
    whole isolation tree (e.g. ~/jarvis/users/<user>/...) is owner-only, not
    just the leaf. Pre-existing dirs outside the created chain are left alone.
    """
    path = Path(path)
    # Ancestors (leaf → rootward) that don't exist yet and will be created.
    created = []
    cur = path
    while not cur.exists():
        created.append(cur)
        cur = cur.parent
    path.mkdir(parents=True, exist_ok=True)
    for p in [path, *created]:
        try:
            os.chmod(p, 0o700)
        except OSError:
            pass
    return path