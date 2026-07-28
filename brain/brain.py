import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from brain.store import fingerprint
from brain.embed import get_embedding
from brain.ingest import chunk_document

DEFAULT_CHAT_MODEL = "llama3.1:8b-instruct-q4_K_M"
_OLLAMA_HOST = "127.0.0.1"
_OLLAMA_PORT = 11434


def _ollama_chat(model: str, messages: list[dict]) -> dict:
    payload = json.dumps({"model": model, "messages": messages, "stream": False}).encode()
    req = urllib.request.Request(
        f"http://{_OLLAMA_HOST}:{_OLLAMA_PORT}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        return {"message": {"content": f"[ollama connection error: {e}]"}}


class Brain:
    def __init__(self, store, model: str = DEFAULT_CHAT_MODEL):
        self.store = store
        self.model = model

    def query(self, user_query: str, n_results: int = 8, source_filter: str | None = None, verbose: bool = False) -> tuple[str, list[dict]]:
        q_emb = get_embedding(user_query)
        memories = self.store.search(q_emb, n_results=n_results, source_filter=source_filter)
        context_parts = []
        for m in memories:
            ctx = f"[{m['source']}] {m['timestamp']}\n{m['content']}"
            context_parts.append(ctx)
        context = "\n\n---\n\n".join(context_parts)
        system_prompt = (
            "You are a private second brain agent. You have access to the user's collected memories below. "
            "Answer using the context provided. If the context is incomplete, say so. Keep answers concise and actionable.\n\n"
            f"RELEVANT MEMORIES:\n{context}"
        )
        response = _ollama_chat(model=self.model, messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ])
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

    def chat(self, session: list[dict], user_message: str):
        if not self._is_substantive(user_message):
            return "Noted.", [], 0
        q_emb = get_embedding(user_message)
        memories = self.store.search(q_emb, n_results=6)
        context_parts = [f"[{m['source']}] {m['timestamp']}\n{m['content']}" for m in memories]
        context = "\n\n---\n\n".join(context_parts)
        session.append({"role": "user", "content": user_message})
        system_prompt = (
            "You are a private second brain. Use the memories below to personalize your answers. "
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
            fid = fingerprint("session", f"chat-{datetime.utcnow().isoformat()}", session_text, datetime.utcnow().isoformat())
            if self.store.exists(fid):
                return False
            emb = get_embedding(session_text)
            tags = ["session", "chat"]
            meta = {"user_turns": user_turns}
            expires = (datetime.utcnow() + timedelta(days=7)).isoformat()
            self.store.add(fid, "session", fid, datetime.utcnow().isoformat(), session_text, tags, meta, emb, tier="session", expires_at=expires)
            return True
        except Exception:
            return False

    def get_recent_activity(self, hours: int = 24, limit: int = 20) -> list[dict]:
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
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

    def remember(self, text: str, source: str = "manual", tags: list[str] | None = None):
        from brain.extract import extract_metadata
        fid = fingerprint(source, "manual", text, datetime.utcnow().isoformat())
        metadata = {"tags": tags or []}
        chunks = chunk_document(text, metadata=metadata)
        emb = get_embedding(text)
        extraction = extract_metadata(text) if not tags else {"tags": tags, "entities": []}
        auto_tags = extraction.get("tags", [])
        all_tags = list(dict.fromkeys((tags or []) + auto_tags))[:10]
        added = 0
        for i, chunk in enumerate(chunks):
            cid = f"{fid}-{i}"
            ct = datetime.utcnow().isoformat()
            chunk_meta = {**metadata, "entities": extraction.get("entities", [])}
            self.store.add(cid, source, "manual", ct, chunk["text"], all_tags, chunk_meta, emb)
            added += 1
        return added

    def correct(self, memory_id: str, correction_text: str):
        if self.store.exists(memory_id):
            self.store.mark_superseded(memory_id)
        fid = fingerprint("correction", memory_id, correction_text, datetime.utcnow().isoformat())
        if self.store.exists(fid):
            return 0
        tags = ["correction", f"correction-of:{memory_id}"]
        meta = {"corrects": memory_id}
        chunks = chunk_document(correction_text, metadata=meta)
        emb = get_embedding(correction_text)
        added = 0
        for i, chunk in enumerate(chunks):
            cid = f"{fid}-{i}"
            ct = datetime.utcnow().isoformat()
            self.store.add(cid, "correction", memory_id, ct, chunk["text"], tags, meta, emb)
            added += 1
        return added

    def upgrade(self, feature_request: str, status: str = "requested") -> int:
        fid = fingerprint("upgrade", feature_request, feature_request, datetime.utcnow().isoformat())
        if self.store.exists(fid):
            return 0
        tags = ["upgrade", status]
        meta = {"status": status}
        chunks = chunk_document(feature_request, metadata=meta)
        emb = get_embedding(feature_request)
        added = 0
        for i, chunk in enumerate(chunks):
            cid = f"{fid}-{i}"
            ct = datetime.utcnow().isoformat()
            self.store.add(cid, "upgrade", feature_request, ct, chunk["text"], tags, meta, emb)
            added += 1
        try:
            upgrades_path = Path("C:/data/second-brain/UPGRADES.md")
            if upgrades_path.exists():
                with open(upgrades_path, "a") as f:
                    ts = datetime.utcnow().strftime("%Y-%m-%d")
                    f.write(f"- `[{status}]` {ts} — {feature_request}\n")
        except Exception:
            pass
        return added
