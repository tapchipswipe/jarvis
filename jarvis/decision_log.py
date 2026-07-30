from pathlib import Path
from datetime import datetime
import json


DECISION_LOG = Path.home() / "jarvis" / "logs" / "decisions.jsonl"


def append_decision(memory_id: str, route: str, confidence: str, envelope: dict, applied: int = 0):
    DECISION_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.utcnow().isoformat(),
        "memory_id": memory_id,
        "route": route,
        "confidence": confidence,
        "envelope": envelope,
        "applied": applied,
    }
    with open(DECISION_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")
