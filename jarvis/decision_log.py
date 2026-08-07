from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _decision_log() -> Path:
    from jarvis.paths import logs_dir
    return logs_dir("decisions.jsonl")


def append_decision(memory_id: str, route: str, confidence: str, envelope: dict, applied: int = 0):
    path = _decision_log()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "memory_id": memory_id,
        "route": route,
        "confidence": confidence,
        "envelope": envelope,
        "applied": applied,
    }
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")

