"""Jarvis user profile — a structured "who you are" onboarding.

Iron-Man's Jarvis knows Stark because it has an *explicit model of who he is*,
not just ambient recall. This module gives your Jarvis that model: a private,
structured profile (name, role, employer, goals, people, projects, preferences,
commitments) stored alongside the token/keys and synced into the brain as
high-weight, never-expiring ``arc``-tier ``source='profile'`` memories plus
knowledge-graph entities — so Jarvis can genuinely *know* you.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Field -> human prompt (used by the guided `init` wizard).
SCALAR_FIELDS = {
    "name": "What should I call you?",
    "role": "Your title / role (e.g. 'software engineer')",
    "employer": "Where you work / study",
    "timezone": "Your timezone",
    "motto": "A motto or one-liner you live by",
}
LIST_FIELDS = {
    "goals": "A goal (short or long term)",
    "people": "Someone important to you, as 'Name: relationship'",
    "projects": "A project you're working on, as 'Name: status'",
    "preferences": "How you like things, as 'thing: preference'",
    "commitments": "A recurring commitment, as 'what: schedule'",
}
ALL_FIELDS = {**SCALAR_FIELDS, **LIST_FIELDS}

DEFAULTS: dict = {f: ("" if f in SCALAR_FIELDS else []) for f in ALL_FIELDS}

# Stable pseudo-date used in the fingerprint so profile entries dedup across
# runs (never re-added on every sync) while still being content-sensitive.
_PROFILE_DATE = "profile-permanent"


def profile_path() -> Path:
    """Location of the profile file (override via JARVIS_PROFILE_FILE)."""
    env = os.environ.get("JARVIS_PROFILE_FILE")
    if env:
        return Path(env)
    return Path.home() / ".config" / "jarvis" / "profile.json"


def load_profile(path: Path | None = None) -> dict:
    """Load the profile with defaults; never raises on a corrupt/missing file."""
    p = path or profile_path()
    profile = dict(DEFAULTS)
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k in ALL_FIELDS:
                    if k not in data:
                        continue
                    if k in SCALAR_FIELDS:
                        profile[k] = str(data[k]).strip()
                    elif isinstance(data[k], list):
                        profile[k] = [
                            str(x).strip() for x in data[k] if str(x).strip()
                        ]
    except Exception:  # noqa: BLE001 - a corrupt profile should never crash callers
        logger.warning("jarvis: could not load profile at %s", p)
    return profile


def save_profile(profile: dict, path: Path | None = None) -> Path:
    """Persist the profile privately (0600 file in a 0700 dir, like token/keys)."""
    p = path or profile_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.parent.chmod(0o700)
    except Exception:  # noqa: BLE001 - best-effort permissions
        pass
    p.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    try:
        p.chmod(0o600)
    except Exception:  # noqa: BLE001 - best-effort permissions
        pass
def profile_entries(profile: dict, only_fields: list[str] | None = None) -> list[dict]:
    """Flatten the profile into per-field memory entries.

    Each entry is ``{field, text, tags}``. ``only_fields`` restricts to a subset
    (used when a single field is set and we only want to re-sync that one).
    """
    fields = only_fields if only_fields is not None else list(ALL_FIELDS)
    entries: list[dict] = []
    for k in fields:
        if k not in ALL_FIELDS:
            continue
        if k in SCALAR_FIELDS:
            val = profile.get(k) or ""
            if val:
                entries.append({"field": k, "text": f"{k}: {val}", "tags": ["profile", k]})
        elif k in LIST_FIELDS:
            for item in profile.get(k) or []:
                if item:
                    entries.append({"field": k, "text": f"{k}: {item}", "tags": ["profile", k]})
    return entries


def profile_digest(profile: dict) -> str:
    """Human-readable dump of the profile (for ``jarvis profile``)."""
    lines = []
    for k in ALL_FIELDS:
        if k in SCALAR_FIELDS and profile.get(k):
            lines.append(f"{k}: {profile[k]}")
        elif k in LIST_FIELDS:
            for item in profile.get(k) or []:
                lines.append(f"{k}: {item}")
    return "\n".join(lines)


def profile_fingerprint(field: str, text: str) -> str:
    """Stable content-aware id for a profile entry (dedups across syncs)."""
    from jarvis.store import fingerprint

    return fingerprint("profile", field, text, _PROFILE_DATE)


def apply_profile_locally(store, profile: dict, only_fields: list[str] | None = None) -> int:
    """Upsert profile entries into a *local* Store as arc-tier, never-expiring
    memories + graph links. Returns the number newly added.

    Embeddings are intentionally left ``None`` (un-embedded) — the box / a later
    ``reindex`` will embed them; this avoids an Ollama round-trip here.
    """
    from jarvis.brain import Brain

    added = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    for ent in profile_entries(profile, only_fields=only_fields):
        fid = profile_fingerprint(ent["field"], ent["text"])
        if store.exists(fid):
            continue
        store.add(
            fid=fid,
            source="profile",
            source_id=ent["field"],
            timestamp=now,
            content=ent["text"],
            tags=ent["tags"],
            metadata={"profile": True, "field": ent["field"]},
            embedding=None,  # embed later via reindex (keeps profile writes cheap)
            tier="arc",
            expires_at=None,
            route="profile",
        )
        added += 1
    if added:
        brain = Brain(store)
        for ent in profile_entries(profile, only_fields=only_fields):
            fid = profile_fingerprint(ent["field"], ent["text"])
            brain._link_entities_to_graph(ent["text"], "profile", [fid])
    return added