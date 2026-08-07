from datetime import datetime, timezone
from pathlib import Path

from jarvis.embed import get_embedding
from jarvis.extract import extract_metadata
from jarvis.ingest import chunk_document
from jarvis.store import fingerprint

HISTORY_PATHS = [
    (Path.home() / ".zsh_history", "zsh"),
    (Path.home() / ".bash_history", "bash"),
    (Path.home() / ".local" / "share" / "fish" / "fish_history", "fish"),
]


def _parse_zsh_line(line: str) -> str | None:
    line = line.strip()
    if not line:
        return None
    if line.startswith(": "):
        parts = line.split(";", 1)
        if len(parts) > 1:
            return parts[1].strip()
    return line


def _parse_fish_line(line: str) -> str | None:
    line = line.strip()
    if line.startswith("- "):
        return line[2:].strip()
    return None


def _parse_bash_line(line: str) -> str | None:
    return line.strip() or None


_PARSERS = {
    "zsh": _parse_zsh_line,
    "fish": _parse_fish_line,
    "bash": _parse_bash_line,
}


def _file_mtime_ts(path: Path) -> str:
    """Stable ISO timestamp for a history file based on its mtime.

    Using the file's mtime (not datetime.now()) keeps the batch fingerprint
    unchanged between runs, so store.exists() fires and unchanged history is
    not re-embedded on every sync.
    """
    mtime = path.stat().st_mtime
    return datetime.fromtimestamp(mtime, tz=timezone.utc).replace(tzinfo=None).isoformat()


def sync_shell(store):
    count = 0
    seen_commands = set()
    for history_path, shell_type in HISTORY_PATHS:
        if not history_path.exists():
            continue
        parser = _PARSERS.get(shell_type)
        if not parser:
            continue
        try:
            # Stable batch ts: use the history file's mtime so the fingerprint is
            # unchanged when the file hasn't changed. A per-command now() made the
            # fingerprint differ every run and the skip (store.exists) never fired.
            ts = _file_mtime_ts(history_path)
            lines = history_path.read_text(errors="ignore").splitlines()
            for line in lines:
                cmd = parser(line)
                if not cmd:
                    continue
                if cmd in seen_commands:
                    continue
                seen_commands.add(cmd)
                source = "shell"
                source_id = cmd[:64]
                fid = fingerprint(source, source_id, cmd, ts)
                if store.exists(fid):
                    continue
                extraction = extract_metadata(cmd)
                base_tags = ["shell", shell_type] + extraction.get("tags", [])[:5]
                chunks = chunk_document(cmd, metadata={"shell": cmd[:100], "shell_type": shell_type, "entities": extraction.get("entities", [])})
                for i, chunk in enumerate(chunks):
                    # First chunk keeps the base fid so store.exists(fid) matches
                    # and the whole (stable-ts) command is skipped on re-sync.
                    cid = fid if i == 0 else f"{fid}-{i}"
                    emb = get_embedding(chunk["text"])
                    store.add(cid, source, source_id, ts, chunk["text"], base_tags, {"command": cmd[:200], "shell_type": shell_type, "entities": extraction.get("entities", [])}, emb)
                    count += 1
        except Exception as e:
            print(f"shell error ({shell_type}): {e}")
    return count

