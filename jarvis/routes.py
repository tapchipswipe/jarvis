
ROUTES = {"idea_capture", "reference_note", "context_list_update", "escalate"}
ROUTE_TAG_MAP = {
    "idea_capture": {"idea"},
    "reference_note": {"reference"},
    "context_list_update": {"action"},
    "escalate": {"escalated"},
    "unclassified": set(),
}
VALID_CONTEXT_LISTS = {"errands.md", "groceries.md", "chores.md", "inbox.md", "dev.md", "health.md"}
ESCALATE_REASON_MAX = 200


def classify_existing(store, memory: dict, model: str = None, dry_run: bool = False) -> dict:
    from jarvis.classifier import apply_envelope, classify, validate_envelope
    content = memory.get("content", "")
    source_id = memory.get("source_id", memory.get("id", "unknown"))
    envelope = classify(content, source_id=source_id, model=model)
    if not validate_envelope(envelope):
        envelope = {
            "route": "escalate",
            "slug": None,
            "source_url_list": [],
            "inbox_path": None,
            "target_list": None,
            "action_atom": None,
            "tag_seeds": [],
            "confidence": "low",
            "escalate_reason": "envelope validation failed after classification; escalated automatically.",
            "notes": None,
        }
    if not dry_run:
        apply_envelope(store, memory["id"], envelope, log=True)
    return envelope
