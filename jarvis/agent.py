"""
jarvis/agent.py - Agent loop with tool use for Jarvis.
Communicates with Ollama via urllib only (no requests library).
"""
import json
import os
import urllib.request
import urllib.error

from jarvis.embed import get_embedding
from jarvis.store import Store
from jarvis.sessions import SessionDB
from jarvis.tools import TOOLS_SCHEMA, execute_tool

_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "100.102.0.99")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


_OLLAMA_PORT = _env_int("OLLAMA_PORT", 11434)
DEFAULT_CHAT_MODEL = "qwen2.5:7b-instruct-q4_K_M"
CHAT_MODEL_FALLBACKS = [
    "qwen2.5:7b-instruct-q4_K_M",
    "qwen2.5:7b-instruct",
    "qwen2.5",
]
MAX_STEPS = 8


def _ollama_url(path: str) -> str:
    return f"http://{_OLLAMA_HOST}:{_OLLAMA_PORT}{path}"

SYSTEM_PROMPT = (
    "You are Jarvis, a private ambient memory agent. You have tools to look up "
    "facts about the user's life, calendar, contacts, and knowledge graph. "
    "Always use tools for factual queries - never invent data. "
    "If a tool returns no results, say so honestly. Be concise and actionable."
)


# ---------------------------------------------------------------------------
# Ollama /api/chat helpers
# ---------------------------------------------------------------------------

def _ollama_chat(model, messages, tools=None, stream=True):
    payload = {
        "model": model,
        "stream": bool(stream),
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _ollama_url("/api/chat"),
        data=data,
        headers={"Content-Type": "application/json"},
    )

    if stream:
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                full_chunks = []
                reader = resp.read1 if hasattr(resp, "read1") else resp.read
                raw = reader(1024 * 1024).decode("utf-8", errors="ignore")
                for line in raw.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    full_chunks.append(chunk)
                    if chunk.get("done"):
                        break
                return {
                    "message": {"content": _concat_chunks(full_chunks)},
                    "_chunks": full_chunks,
                }
        except (urllib.error.URLError, urllib.error.HTTPError):
            # Fallback: non-streaming
            payload["stream"] = False
            data2 = json.dumps(payload).encode("utf-8")
            req2 = urllib.request.Request(
                _ollama_url("/api/chat"),
                data=data2,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req2, timeout=180) as resp2:
                    resp_data = json.loads(resp2.read().decode())
                    return {"message": resp_data.get("message", {"content": ""})}
            except (urllib.error.URLError, urllib.error.HTTPError) as e:
                return {"message": {"content": f"[ollama connection error: {e}]"}}
    else:
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                resp_data = json.loads(resp.read().decode())
                return {"message": resp_data.get("message", {"content": ""})}
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            return {"message": {"content": f"[ollama connection error: {e}]"}}


def _concat_chunks(chunks) -> str:
    parts = []
    for c in chunks:
        content = c.get("message", {}).get("content")
        if content:
            parts.append(str(content))
    return "".join(parts)

def _extract_text(response) -> str:
    if not isinstance(response, dict):
        return ""
    msg = response.get("message", {})
    content = msg.get("content", "") if isinstance(msg, dict) else ""
    if content:
        return str(content)
    # Fall back to concatenating streamed chunks
    chunks = response.get("_chunks")
    if chunks:
        return _concat_chunks(chunks)
    return ""

def _parse_tool_call(response):
    if not isinstance(response, dict):
        return None
    msg = response.get("message", {})
    if not isinstance(msg, dict):
        return None
    tool_calls = msg.get("tool_calls")
    if not tool_calls:
        return None
    first = tool_calls[0]
    if "function" in first:
        name = first.get("function", {}).get("name", "")
        args = first.get("function", {}).get("arguments", {})
    else:
        name = first.get("name", "")
        args = first.get("arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            args = {}
    if not name:
        return None
    return name, args


def resolve_chat_models(override: str | None = None) -> list[str]:
    """Return the ordered list of chat models to try.

    Priority: explicit override > JARVIS_CHAT_MODEL (comma-separated) >
    JARVIS_CHAT_MODEL_FALLBACKS (comma-separated) > CHAT_MODEL_FALLBACKS.
    """
    if override:
        return [override]
    env_model = os.environ.get("JARVIS_CHAT_MODEL")
    if env_model:
        models = [m.strip() for m in env_model.split(",") if m.strip()]
        if models:
            return models
    env_fallbacks = os.environ.get("JARVIS_CHAT_MODEL_FALLBACKS")
    if env_fallbacks:
        models = [m.strip() for m in env_fallbacks.split(",") if m.strip()]
        if models:
            return models
    return list(CHAT_MODEL_FALLBACKS)


def _chat_with_fallback(messages, tools=None, stream=True, model_override=None):
    """Call Ollama, trying each configured model until one responds.

    A model that returns a connection error (unavailable/not pulled) is
    skipped in favor of the next model in the fallback list.
    """
    errors = []
    for model in resolve_chat_models(model_override):
        resp = _ollama_chat(model, messages, tools=tools, stream=stream)
        if "[ollama connection error" in _extract_text(resp):
            errors.append(_extract_text(resp))
            continue
        return resp
    return {"message": {"content": errors[-1] if errors else ""}}


def _inject_rag_context(session_db, user_message, model: str | None = None) -> str:
    store = None
    try:
        store = Store()
        emb = get_embedding(user_message, model=model or "nomic-embed-text")
        rows = store.search(emb, n_results=5)
        if not rows:
            return ""
        parts = ["RELEVANT MEMORIES:"]
        for r in rows:
            parts.append(
                f"[" + r.get("source", "") + "] " + "\n" +
                r.get("timestamp", "") + "\n" + r.get("content", "")
            )
        # Surface linked entities so the agent can reason over the graph.
        links = store.lookup_entities([r.get("id") for r in rows])
        if links:
            ent_lines = []
            for ents in links.values():
                ent_lines.extend(e["name"] for e in ents)
            parts.append("RELATED ENTITIES: " + ", ".join(ent_lines))
        return "\n".join(parts)
    except Exception:
        return ""
    finally:
        if store:
            store.close()


def run_turn(
    user_message,
    session_id,
    max_steps=8,
    session_db=None,
    store_db=None,
    verbose=False,
    model: str | None = None,
) -> tuple:
    """
    Run one agent turn and return (answer, tool_call_log).
    Signature: run_turn(user_message, session_id, max_steps=8).
    session_db and store_db are optional keyword args; auto-created
    when not provided for direct/standalone script usage.
    """
    owns_session_db = session_db is None
    if session_db is None:
        session_db = SessionDB()

    # Open memory store once if not provided
    store = store_db if store_db is not None else Store()
    should_close_store = store_db is None

    try:
        # 1. Ensure session exists
        session = session_db.get_session(session_id)
        if not session:
            session_id = session_db.create_session(title="Chat", tier="raw")

        # 2. Append user message to session
        session_db.append_message(session_id, "user", user_message)

        # 3. Load full history
        raw_history = session_db.get_messages(session_id, limit=100)

        # 4. Rebuild ollama_messages from history
        ollama_messages = []
        for m in raw_history:
            role = m.get("role")
            content = m.get("content", "")
            if role not in ("user", "assistant", "system", "tool"):
                continue
            msg = {"role": role, "content": content}
            tool_calls = m.get("tool_calls")
            if role == "assistant" and tool_calls:
                msg["tool_calls"] = [
                    {
                        "name": tc.get("name", ""),
                        "arguments": tc.get("arguments", {}),
                    }
                    for tc in tool_calls
                ]
            ollama_messages.append(msg)

        # 5. RAG injection + system prompt (ONCE per turn)
        rag = _inject_rag_context(session_db, user_message, model=model)
        system_messages = []
        if raw_history:
            block = SYSTEM_PROMPT
            if rag:
                block += "\n"*2 + "INJECTED CONTEXT:" + rag + "\n"*2
                block += "You may use tools to get more info."
            system_messages.append({"role": "system", "content": block})
        else:
            system_messages.append({"role": "system", "content": SYSTEM_PROMPT})
            if rag:
                system_messages.append({
                    "role": "system",
                    "content": "INJECTED CONTEXT:" + rag + "\n\n" + 
                      "  You may use tools to get more info.",
                })
        ollama_messages = system_messages + ollama_messages

        tool_log = []

        # 6. Main agent loop - stream=True, fallback built into _ollama_chat
        for step in range(max_steps):
            resp = _chat_with_fallback(
                ollama_messages, tools=TOOLS_SCHEMA, stream=True,
                model_override=model,
            )

            text = _extract_text(resp)
            tool_call = _parse_tool_call(resp)

            if tool_call:
                tool_name, tool_args = tool_call
                result = execute_tool(tool_name, session_db, tool_args)

                # Record assistant message + tool_calls in session
                session_db.append_message(
                    session_id, "assistant", text or "",
                    tool_calls=[{"name": tool_name, "arguments": tool_args}],
                )
                # Record tool result
                session_db.append_message(session_id, "tool", json.dumps(result))

                tool_log.append({
                    "tool": tool_name,
                    "args": tool_args,
                    "result": result,
                    "step": step,
                })

                # Append to ollama history for next iteration
                ollama_messages.append({
                    "role": "assistant",
                    "content": text or "",
                    "tool_calls": [{"name": tool_name, "arguments": tool_args}],
                })
                ollama_messages.append({
                    "role": "tool",
                    "content": json.dumps(result),
                })
                continue

            # Final text response
            session_db.append_message(session_id, "assistant", text)
            session_db.update_session(session_id)
            return text, tool_log

        # Max steps hit
        final_text = "[Step limit reached]"
        session_db.append_message(session_id, "assistant", final_text)
        session_db.update_session(session_id)
        return final_text, tool_log

    finally:
        if should_close_store:
            store.close()
        if owns_session_db:
            session_db.close()