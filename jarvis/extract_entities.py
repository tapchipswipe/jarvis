"""
jarvis/extract_entities.py — Lightweight, source-aware entity extraction.

No external NLP dependencies; uses regex + heuristics that degrade
gracefully when patterns aren't found.

Returns a list of dicts:
    {
        "name": str,
        "type": str,       # person | org | domain
        "confidence": float
    }
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")
_URL_RE = re.compile(r"https?://[^\s>)]+")
_NAME_RE = re.compile(
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)"  # Title Case words
)
_TITLE_PREFIX = re.compile(r"^(Mr|Mrs|Ms|Dr|Prof)\s+", re.IGNORECASE)


def _clean_name(name: str) -> str:
    n = _TITLE_PREFIX.sub("", name).strip()
    return n


def _dedupe(ents: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for e in ents:
        key = (e["name"].lower(), e["type"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


# ---------------------------------------------------------------------------
# Source-specific extractors
# ---------------------------------------------------------------------------


def _from_contacts(text: str) -> list[dict]:
    # Contacts already carry structured names; look for proper-name lines
    ents: list[dict] = []
    for m in _NAME_RE.finditer(text):
        raw = _clean_name(m.group(1))
        if len(raw.split()) >= 2:
            ents.append({"name": raw, "type": "person", "confidence": 0.95})
    return _dedupe(ents)


def _from_calendar(text: str) -> list[dict]:
    ents: list[dict] = []
    # Attendees often appear in parentheses or after 'with'
    attendee_block = re.findall(
        r"(?:Attendees?|with|guests?)[:\s]+([^\n]+)", text, re.IGNORECASE
    )
    block_text = " ".join(attendee_block)
    for m in _NAME_RE.finditer(block_text):
        raw = _clean_name(m.group(1))
        if len(raw.split()) >= 2:
            ents.append({"name": raw, "type": "person", "confidence": 0.90})
    # Event titles often contain org keywords
    org_hints = re.findall(
        r"(?i)\b(team|dept|department|division|inc|llc|university|college|labs?|foundation)\b",
        text,
    )
    for _ in org_hints:
        ents.append({"name": "Organization reference", "type": "org", "confidence": 0.45})
    return _dedupe(ents)


def _from_email(text: str) -> list[dict]:
    ents: list[dict] = []
    # From:
    m = re.search(r"^From:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    if m:
        raw = _clean_name(m.group(1).split("<")[0].strip())
        if raw:
            ents.append({"name": raw, "type": "person", "confidence": 0.95})
    # To:
    to_block = re.search(r"^To:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    if to_block:
        raw = to_block.group(1)
        for part in re.split(r"[,;]", raw):
            name = _clean_name(part.split("<")[0].strip())
            if name:
                ents.append({"name": name, "type": "person", "confidence": 0.90})
    # Sender email -> domain as org
    for addr in _EMAIL_RE.findall(text):
        domain = addr.split("@")[-1]
        ents.append({"name": domain, "type": "domain", "confidence": 0.60})
    # Also scan body for proper names (Title Case two-word sequences)
    for m in _NAME_RE.finditer(text):
        raw = _clean_name(m.group(1))
        if len(raw.split()) >= 2:
            existing_names = {e["name"].lower() for e in ents if e["type"] == "person"}
            if raw.lower() not in existing_names:
                ents.append({"name": raw, "type": "person", "confidence": 0.60})
    return _dedupe(ents)


def _from_browser(text: str) -> list[dict]:
    ents: list[dict] = []
    # Domains as org entities
    for m in _URL_RE.finditer(text):
        try:
            domain = urlparse(m.group()).netloc.lower()
            if domain:
                ents.append({"name": domain, "type": "domain", "confidence": 0.80})
        except Exception:
            pass
    # Org hints in page title
    org_hints = re.findall(
        r"(?i)\b(team|dept|department|division|inc|llc|university|college|labs?|foundation|company|corp|group)\b",
        text,
    )
    for _ in org_hints:
        ents.append({"name": "Organization reference", "type": "org", "confidence": 0.40})
    return _dedupe(ents)


def _from_chat(text: str) -> list[dict]:
    ents: list[dict] = []
    # Chat messages are typically prefixed by speaker name in [brackets]
    bracket_speakers = re.findall(r"\[([^\]]+)\]", text)
    for sp in bracket_speakers:
        cleaned = sp.strip()
        if not cleaned:
            continue
        # if it looks like a name (contains a letter)
        if re.search(r"[A-Za-z]", cleaned):
            ents.append({"name": cleaned, "type": "person", "confidence": 0.85})
    # Names line-prefixed (e.g. "Alice: hello")
    line_speakers = re.findall(r"^(\w+)\s*:", text, re.MULTILINE)
    for sp in line_speakers[:10]:
        if len(sp) > 1 and sp.lower() not in ("http", "https"):
            ents.append({"name": sp, "type": "person", "confidence": 0.75})
    return _dedupe(ents)


def _fallback(text: str) -> list[dict]:
    ents: list[dict] = []
    for m in _NAME_RE.finditer(text):
        raw = _clean_name(m.group(1))
        if len(raw.split()) >= 2 and len(raw) < 60:
            ents.append({"name": raw, "type": "person", "confidence": 0.50})
    for addr in _EMAIL_RE.findall(text):
        ents.append({"name": addr.split("@")[-1], "type": "domain", "confidence": 0.45})
    return _dedupe(ents)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

EXTRACTORS = {
    "contacts": _from_contacts,
    "calendar": _from_calendar,
    "email":    _from_email,
    "browser":  _from_browser,
    "chat":     _from_chat,
}


def extract_entities(text: str, source_type: str = "manual") -> list[dict]:
    """Return a de-duplicated list of entity dicts detected in *text*."""
    if not text:
        return []
    extractor = EXTRACTORS.get(source_type.lower(), _fallback)
    try:
        ents = extractor(text)
    except Exception as exc:
        logger.debug("Entity extraction failed for %s: %s", source_type, exc)
        ents = []
    if not ents:
        ents = _fallback(text)
    return _dedupe(ents)
