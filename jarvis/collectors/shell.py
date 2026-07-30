from pathlib import Path
from datetime import datetime
from jarvis.store import fingerprint
from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.extract import extract_metadata

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
                ts = datetime.utcnow().isoformat()
                fid = fingerprint(source, source_id, cmd, ts)
                if store.exists(fid):
                    continue
                extraction = extract_metadata(cmd)
                base_tags = ["shell", shell_type] + extraction.get("tags", [])[:5]
                chunks = chunk_document(cmd, metadata={"shell": cmd[:100], "shell_type": shell_type, "entities": extraction.get("entities", [])})
                emb = get_embedding(cmd[:4000])
                for i, chunk in enumerate(chunks):
                    cid = f"{fid}-{i}"
                    store.add(cid, source, source_id, ts, chunk["text"], base_tags, {"command": cmd[:200], "shell_type": shell_type, "entities": extraction.get("entities", [])}, emb)
                    count += 1
        except Exception as e:
            print(f"shell error ({shell_type}): {e}")
    return count
