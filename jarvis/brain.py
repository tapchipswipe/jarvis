import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from jarvis.embed import get_embedding
from jarvis.ingest import chunk_document
from jarvis.store import fingerprint

DEFAULT_CHAT_MODEL = "qwen2.5:7b-instruct-q4_K_M"
CHAT_TIERS = ("fast", "medium", "big")


def _stable_day() -> str:
    """Stable day key (YYYY-MM-DD) used in fingerprints so re-runs within the
    same day produce the same fingerprint and store.exists() dedup fires. A
    live clock timestamp in the fingerprint made every re-run distinct, so
    re-saving a session / re-applying a correction / re-logging a feature
    request silently created duplicates."""
    return datetime.now(timezone.utc).replace(tzinfo=None).date().isoformat()


def chat_model() -> str:
    """Active chat/query model — configurable via JARVIS_CHAT_MODEL.

    On the RAM-tight box the 7B is slow (20-60s per answer); set
    JARVIS_CHAT_MODEL to a small model (e.g. llama3.2:1b) for snappy
    ask/console/chat responses. Defaults to the 7B."""
    return os.environ.get("JARVIS_CHAT_MODEL", "").strip() or DEFAULT_CHAT_MODEL


def tier_model(tier: str) -> str:
    """The model for a named tier (fast/medium/big) from env.

      JARVIS_CHAT_MODEL_FAST   -> small/snappy (e.g. qwen2.5:0.5b)
      JARVIS_CHAT_MODEL_BIG    -> large/high-quality (e.g. qwen2.5:7b)
      JARVIS_CHAT_MODEL        -> medium (default)
    Any unset tier falls back to the medium model."""
    if tier == "fast":
        return os.environ.get("JARVIS_CHAT_MODEL_FAST", "").strip() or chat_model()
    if tier == "big":
        return os.environ.get("JARVIS_CHAT_MODEL_BIG", "").strip() or chat_model()
    return chat_model()


def _is_recall_question(question: str) -> bool:
    """True when the question asks about the user's own data/memories (needs a
    real model to summarize — never the fast toy model)."""
    q = (question or "").lower()
    return any(k in q for k in (
        "what did", "what have", "when did", "where did", "remember", "find ",
        "search", "my memories", "my notes", "show me", "tell me about",
        "what do you know about", "recap", "summarize", " notes on",
        "what about", "list the", "look up", "lookup"))


def _tier_for(question: str) -> str:
    """Route a question to a model tier by lightweight complexity heuristics.

    Hard-looking (long or obviously-hard) -> big; recall (about the user's own
    data) -> medium at minimum (never fast); casual/short -> fast; otherwise
    medium."""
    q = (question or "").strip().lower()
    words = q.split()
    hard = any(kw in q for kw in (
        "explain", "analyze", "compare", "why does", "how does", "summarize",
        "write ", "design", "plan", "debug", "refactor", "architecture",
        "implement", "review", "fix ", "step by step", "what is the best",
        "how do i", "explain the difference"))
    if len(words) >= 25 or hard:
        return "big"
    if _is_recall_question(q):
        return "medium"
    casual = any(kw in q for kw in (
        "hello", " hey", "hi ", "thanks", "good morning", "good evening",
        "good day", "how are you", "who are you", "what can you do",
        "nice to", "thank you", "yo", "what's up"))
    if casual or len(words) <= 6:
        return "fast"
    return "medium"


def select_model_for(question: str, force: str | None = None) -> str:
    """Pick a chat model for a question: auto-tier by complexity, or force a
    specific tier (force in fast/medium/big) or an exact model id.

    Guardrail: recall questions (about the user's own data) NEVER use the fast
    toy model — even if fast is forced — because it can't summarize memories;
    they escalate to at least medium."""
    if force and force not in CHAT_TIERS:
        return force  # explicit model id
    if force == "fast" and _is_recall_question(question):
        force = None  # escalate: recall needs a real model
    tier = force if force in CHAT_TIERS else _tier_for(question)
    return tier_model(tier)
_OLLAMA_HOST = "127.0.0.1"
_OLLAMA_PORT = 11434

logger = logging.getLogger("jarvis.brain")


def _digest_model() -> str:
    """Model used for morning/end-of-day digests.

    Configurable via ``JARVIS_DIGEST_MODEL`` (defaults to the chat model). The
    box is RAM-tight; for digests prefer a SMALL model (e.g. JARVIS_DIGEST_MODEL
    =qwen2.5:3b) and never keep a large one resident alongside a running 7B.
    """
    digest_model = os.environ.get("JARVIS_DIGEST_MODEL", "").strip() or DEFAULT_CHAT_MODEL
    if any(tok in digest_model.lower() for tok in ("7b", "8b", "13b", "70b")):
        logger.warning(
            "digest model '%s' is a large tier — set JARVIS_DIGEST_MODEL to a "
            "small model for RAM discipline on the box", digest_model)
    return digest_model


def _ollama_chat(model: str, messages: list[dict]) -> dict:
    # Host/port read from env at call time (defaults 127.0.0.1:11434), so a
    # thin client can point the brain at the box's Ollama for out-of-band runs.
    host = os.environ.get("OLLAMA_HOST", "") or _OLLAMA_HOST
    port = os.environ.get("OLLAMA_PORT", "") or _OLLAMA_PORT
    prompt = _messages_to_prompt(messages)
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        f"http://{host}:{port}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode())
            return {"message": {"content": data.get("response", "")}}
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        return {"message": {"content": f"[ollama connection error: {e}]"}}


def _messages_to_prompt(messages: list[dict]) -> str:
    parts = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            parts.append(f"System: {content}")
        elif role == "user":
            parts.append(f"User: {content}")
        elif role == "assistant":
            parts.append(f"Assistant: {content}")
    return "\n\n".join(parts)


class Brain:
    def __init__(self, store, model: str | None = None):
        self.store = store
        self.model = model or chat_model()

    def query(self, user_query: str, n_results: int = 8, source_filter: str | None = None, verbose: bool = False, history: list | None = None) -> tuple[str, list[dict]]:
        q_emb = get_embedding(user_query)
        memories = self.store.search(q_emb, n_results=n_results, source_filter=source_filter)
        context_parts = []
        for m in memories:
            ctx = f"[{m['source']}] {m['timestamp']}\n{m['content']}"
            context_parts.append(ctx)
        context = "\n\n---\n\n".join(context_parts)

        # Surface the knowledge graph: link any entities found on these memories.
        links = self.store.lookup_entities([m["id"] for m in memories]) if memories else {}
        linked = ""
        if links:
            lines_txt = []
            for mid, ents in links.items():
                lines_txt.append(f"- {', '.join(e['name'] for e in ents)}")
            linked = "\n\nRELATED ENTITIES:\n" + "\n".join(lines_txt)

        system_prompt = (
            "You are a private Jarvis agent. You have access to the user's collected memories below. "
            "Answer using the context provided. If the context is incomplete, say so. Keep answers concise and actionable.\n\n"
            f"RELEVANT MEMORIES:\n{context}{linked}"
        )
        messages = [{"role": "system", "content": system_prompt}]
        # Optional thread: prior turns from the session keep follow-ups coherent.
        if history:
            for turn in history[-20:]:
                role = turn.get("role")
                content = turn.get("content")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_query})
        response = _ollama_chat(model=self.model, messages=messages)
        answer = response.get("message", {}).get("content", "[no response]")
        if verbose:
            conf = self._confidence(memories)
            answer = f"[confidence: {conf}]\n\n{answer}"
        return answer, memories

    def _confidence(self, memories: list[dict]) -> str:
        if not memories:
            return "low"
        weights = [m.get("weight", 0.3) for m in memories[:3]]
        avg = sum(weights) / len(weights)
        if avg >= 1.0:
            return "high"
        if avg >= 0.6:
            return "medium"
        return "low"

    def build_digest(self, kind: str = "morning_brief", hours: int | None = None,
                     max_memories: int = 12) -> str:
        """Synthesize a morning / end-of-day digest from recent memories and
        the Mayor task queue. Falls back to a static bullet list on LLM failure.
        """
        if kind == "morning_brief":
            hours = hours if hours is not None else 12
            system_prompt = (
                "You are Jarvis. Write a concise, friendly morning digest from the "
                "overnight memories and task state below. Use 3-5 short bullets. "
                "Do not invent facts. If nothing is listed, say so in one line."
            )
        else:
            hours = hours if hours is not None else 24
            system_prompt = (
                "You are Jarvis. Write a concise end-of-day wrap-up from the "
                "memories and task state below: what happened today and what is "
                "still open. Use 3-5 short bullets. Do not invent facts."
            )

        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)).isoformat()
        rows = self.store.conn.execute(
            "SELECT source, content, timestamp FROM memories"
            " WHERE superseded = 0 AND timestamp >= ?"
            " ORDER BY timestamp DESC LIMIT ?",
            (cutoff, max_memories),
        ).fetchall()
        mems = [dict(r) for r in rows]

        pending = in_progress = 0
        task_titles: list[str] = []
        try:
            from jarvis.task_queue import TaskQueue
            tq = TaskQueue()
            try:
                pending = len(tq.list_tasks(status="pending_review"))
                in_progress = len(tq.list_tasks(status="in_progress"))
                task_titles = [t.get("title", "") for t in tq.list_tasks(limit=8)]
            finally:
                tq.close()
        except Exception:
            pass

        if not mems and pending == 0 and in_progress == 0:
            return f"No new activity in the last {hours}h and no pending tasks."

        user_text = "RECENT MEMORIES:\n" + "\n".join(
            f"- [{m['source']}] {m['content'][:200]}" for m in mems[:max_memories]
        )
        user_text += (
            f"\n\nTASKS - pending: {pending}, in progress: {in_progress}\n"
            + "\n".join(f"- {t}" for t in task_titles[:5])
        )

        def _try(model: str) -> str:
            resp = _ollama_chat(model, messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ])
            text = (resp.get("message") or {}).get("content", "").strip()
            # An "[ollama ...]" content means the call failed (e.g. model absent
            # / ollama down) — treat as no answer rather than digesting the error.
            if text and not text.startswith("[ollama"):
                return text
            return ""

        digest_model = _digest_model()
        text = _try(digest_model)
        if not text and digest_model != self.model:
            # Fall back to the chat model in case the small override is absent.
            text = _try(self.model)
        if text:
            return text

        lines = [f"- [{m['source']}] {m['content'][:120]}" for m in mems[:8]]
        return "\n".join(lines) or "No new memories in this window."

    def chat(self, session: list[dict], user_message: str):
        if not self._is_substantive(user_message):
            return "Noted.", [], 0
        q_emb = get_embedding(user_message)
        memories = self.store.search(q_emb, n_results=6)
        context_parts = [f"[{m['source']}] {m['timestamp']}\n{m['content']}" for m in memories]
        context = "\n\n---\n\n".join(context_parts)
        session.append({"role": "user", "content": user_message})
        system_prompt = (
            "You are a private Jarvis. Use the memories below to personalize your answers. "
            "Always cite sources with [source] and timestamp when available. "
            "If you are unsure based on available memory, say so honestly.\n\n"
            f"RELEVANT MEMORIES:\n{context}"
        )
        messages = [{"role": "system", "content": system_prompt}] + session[-20:]
        response = _ollama_chat(model=self.model, messages=messages)
        session.append({"role": "assistant", "content": response.get("message", {}).get("content", "")})
        answer = response.get("message", {}).get("content", "[no response]")
        conf = self._confidence(memories)
        source_count = len(memories)
        return f"[confidence: {conf}] {answer}", memories, source_count

    def save_session(self, session: list[dict]) -> bool:
        user_turns = sum(1 for m in session if m["role"] == "user")
        if user_turns < 3:
            return False
        try:
            session_text = "\n".join(f"{m['role']}: {m['content']}" for m in session)
            # Anchor the fingerprint on session identity (first user message) +
            # stable day, not a live clock timestamp, so re-saving the same
            # session dedups via store.exists() instead of creating a duplicate.
            day = _stable_day()
            first_user = next((m["content"] for m in session if m["role"] == "user"), "")[:120]
            session_key = f"chat-{day}-{first_user}"
            fid = fingerprint("session", session_key, session_text, day)
            if self.store.exists(fid):
                return False
            emb = get_embedding(session_text)
            tags = ["session", "chat"]
            meta = {"user_turns": user_turns}
            expires = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=7)).isoformat()
            self.store.add(fid, "session", fid, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), session_text, tags, meta, emb, tier="session", expires_at=expires)
            self._link_entities_to_graph(session_text, "session", [fid])
            return True
        except Exception:
            return False

    def get_recent_activity(self, hours: int = 24, limit: int = 20) -> list[dict]:
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)).isoformat()
        cur = self.store.conn.execute(
            "SELECT * FROM memories WHERE timestamp >= ? AND superseded = 0 ORDER BY timestamp DESC LIMIT ?",
            (cutoff, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def _is_substantive(self, text: str) -> bool:
        text = text.strip()
        if len(text) < 5:
            return False
        trivial = {"hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "sure", "yep", "nope", "no", "yes"}
        if text.lower() in trivial:
            return False
        return True

    def _link_entities_to_graph(self, text: str, source: str, memory_ids: list[str]) -> None:
        """Best-effort: extract entities from *text* and link them into the graph.

        Manual/session/consolidated memories used to write extracted entities
        only into chunk metadata but never into the knowledge graph, so the most
        'Jarvis' memories were invisible to get_related / get_entity_timeline.
        This closes that gap: run extract_entities, exact-match each entity via
        get_or_create_entity + link_memory_entity (no fuzzy, to avoid spurious
        merges / dupes), then infer co-participant edges among the affected
        memories.

        Deliberately best-effort: any graph failure is logged and swallowed so
        the memory write itself is never jeopardized.
        """
        if not text or not memory_ids:
            return
        try:
            from jarvis.extract_entities import extract_entities
            from jarvis.graph import infer_relationships
            ents = extract_entities(text, source_type=source)
            if not ents:
                return
            for ent in ents:
                eid = self.store.get_or_create_entity(
                    ent["name"], entity_type=ent.get("type", "person")
                )
                if not eid:
                    continue
                for mid in memory_ids:
                    self.store.link_memory_entity(
                        mid, eid, confidence=ent.get("confidence", 1.0)
                    )
            # Generate co_participant edges among the entities co-occurring in
            # these memories (explicit ids -> works for any tier, not just raw).
            infer_relationships(self.store, memory_ids=list(memory_ids))
        except Exception as exc:
            logger.warning("Entity graph linking skipped for %s: %s", source, exc)

    def remember(self, text: str, source: str = "manual", tags: list[str] | None = None, classify: bool = False):
        from jarvis.extract import extract_metadata
        fid = fingerprint(source, "manual", text, datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
        metadata = {"tags": tags or []}
        chunks = chunk_document(text, metadata=metadata)
        extraction = extract_metadata(text) if not tags else {"tags": tags, "entities": []}
        auto_tags = extraction.get("tags", [])
        all_tags = list(dict.fromkeys((tags or []) + auto_tags))[:10]
        added = 0
        chunk_ids: list[str] = []
        for i, chunk in enumerate(chunks):
            cid = f"{fid}-{i}"
            ct = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            chunk_meta = {**metadata, "entities": extraction.get("entities", [])}
            emb = get_embedding(chunk["text"])
            self.store.add(cid, source, "manual", ct, chunk["text"], all_tags, chunk_meta, emb)
            chunk_ids.append(cid)
            added += 1
        if added > 0:
            self._link_entities_to_graph(text, source, chunk_ids)
        if classify and added > 0:
            primary_id = f"{fid}-0"
            row = self.store.conn.execute("SELECT content, source_id FROM memories WHERE id = ?", (primary_id,)).fetchone()
            if row:
                from jarvis.routes import classify_existing
                classify_existing(self.store, {"id": primary_id, "content": row["content"], "source_id": row["source_id"]})
        return added

    def classify_memory(self, memory_id: str) -> dict:
        row = self.store.conn.execute("SELECT * FROM memories WHERE id = ? AND superseded = 0", (memory_id,)).fetchone()
        if not row:
            return {}
        from jarvis.routes import classify_existing
        return classify_existing(self.store, dict(row))

    def correct(self, memory_id: str, correction_text: str):
        if self.store.exists(memory_id):
            self.store.mark_superseded(memory_id)
        fid = fingerprint("correction", memory_id, correction_text, _stable_day())
        if self.store.exists(f"{fid}-0"):
            return 0
        tags = ["correction", f"correction-of:{memory_id}"]
        meta = {"corrects": memory_id}
        chunks = chunk_document(correction_text, metadata=meta)
        added = 0
        for i, chunk in enumerate(chunks):
            cid = f"{fid}-{i}"
            ct = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            emb = get_embedding(chunk["text"])
            self.store.add(cid, "correction", memory_id, ct, chunk["text"], tags, meta, emb)
            added += 1
        return added

    def upgrade(self, feature_request: str, status: str = "requested") -> int:
        fid = fingerprint("upgrade", feature_request, feature_request, _stable_day())
        if self.store.exists(f"{fid}-0"):
            return 0
        tags = ["upgrade", status]
        meta = {"status": status}
        chunks = chunk_document(feature_request, metadata=meta)
        added = 0
        for i, chunk in enumerate(chunks):
            cid = f"{fid}-{i}"
            ct = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            emb = get_embedding(chunk["text"])
            self.store.add(cid, "upgrade", feature_request, ct, chunk["text"], tags, meta, emb)
            added += 1
        try:
            from jarvis.paths import config_file
            upgrades_path = config_file("UPGRADES.md")
            if upgrades_path.exists():
                with open(upgrades_path, "a") as f:
                    ts = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
                    f.write(f"- `[{status}]` {ts} — {feature_request}\n")
        except Exception:
            pass
        return added

