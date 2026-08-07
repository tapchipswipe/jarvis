"""
jarvis/mayor.py — The Mayor orchestrator daemon.

Runs 24/7 on Lightspeed (Dell G7). Receives ideas via HTTP, parses them
into tasks using llama3.2:1b (always loaded), queues them for review,
and dispatches approved tasks to coding agents.

Day mode (8am-11pm):  qwen2.5-coder:14b loaded → agents work on tasks
Night mode (11pm-8am): qwen2.5:7b loaded → Jarvis memory daemon runs

HTTP API (port 8767):
  POST /idea          — submit a raw idea (from dashboard/phone/shortcut)
  GET  /status        — mayor status + task queue stats
  GET  /tasks          — list tasks (optional ?status= filter)
  POST /tasks/approve  — approve a task (?id= or ?all=true)
  POST /tasks/reject   — reject a task (?id=)
  GET  /health         — simple health check
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jarvis.task_queue import TaskQueue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("jarvis.mayor")

# ── Configuration ────────────────────────────────────────────────────────────

MAYOR_PORT = int(os.environ.get("MAYOR_PORT", "8767"))
OLLAMA_URL = f"http://{os.environ.get('OLLAMA_HOST', '100.102.0.99')}:{os.environ.get('OLLAMA_PORT', '11434')}"
PROJECT_ROOT = Path(os.environ.get("JARVIS_ROOT", str(Path.home() / "jarvis")))

DAY_START = 8   # 8am
DAY_END = 23    # 11pm

IDEA_PARSER_MODEL = "llama3.2:1b"
CODING_MODEL = "qwen2.5:7b-instruct-q4_K_M"
MEMORY_MODEL = "qwen2.5:7b-instruct-q4_K_M"

POLL_INTERVAL = 30  # seconds between task queue checks
AUTO_APPROVE_TIMEOUT = 7200  # auto-approve after 2 hours (seconds)

# ── Mode switching ──────────────────────────────────────────────────────────

def get_mode(now: datetime | None = None) -> str:
    """Return 'coding' or 'memory' based on current time."""
    if now is None:
        now = datetime.now()
    hour = now.hour
    if DAY_START <= hour < DAY_END:
        return "coding"
    return "memory"


def unload_model(model: str):
    """Unload a model from Ollama to free VRAM."""
    try:
        payload = json.dumps({"model": model, "keep_alive": 0}).encode()
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        logger.info("Unloaded model: %s", model)
    except Exception as e:
        logger.debug("Could not unload %s: %s", model, e)


def ensure_model_loaded(model: str) -> bool:
    """Ensure a model is loaded by making a tiny request with keep_alive."""
    try:
        payload = json.dumps({
            "model": model,
            "prompt": "hi",
            "stream": False,
            "keep_alive": "30m",
            "options": {"num_predict": 1},
        }).encode()
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=120)
        logger.info("Model loaded: %s", model)
        return True
    except Exception as e:
        logger.error("Could not load %s: %s", model, e)
        return False

# ── Idea parser ─────────────────────────────────────────────────────────────

PARSE_PROMPT_TEMPLATE = """You are a task parser. Read the user's idea and output a JSON object with these fields:
{{
  "agent": "code" | "design" | "qa" | "security" | "research",
  "title": "Short task title (max 80 chars)",
  "description": "What needs to be done (1-3 sentences)",
  "priority": 1-5 (1=urgent, 5=low)
}}

Agent selection guide:
- code: implementing features, fixing bugs, writing Python code
- design: dashboard UI, CSS, visualizations, HTML changes
- qa: testing, verifying code works, running tests
- security: scanning for vulnerabilities, secrets, security audits
- research: architecture planning, documentation, codebase analysis

User's idea: {idea}

Output ONLY the JSON object, no other text."""


def parse_idea(idea: str) -> dict | None:
    """Use llama3.2:1b to parse a raw idea into a structured task."""
    prompt = PARSE_PROMPT_TEMPLATE.format(idea=idea)
    payload = json.dumps({
        "model": IDEA_PARSER_MODEL,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "30m",
        "format": "json",
        "options": {"temperature": 0.1},
    }).encode()

    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            response_text = result.get("response", "")
            # The format:json parameter should return valid JSON
            task = json.loads(response_text)
            # Validate required fields
            if "agent" in task and "title" in task:
                # Clamp priority to 1-5
                task["priority"] = max(1, min(5, int(task.get("priority", 3))))
                # Validate agent
                valid_agents = {"code", "design", "qa", "security", "research"}
                if task["agent"] not in valid_agents:
                    task["agent"] = "code"
                return task
    except Exception as e:
        logger.error("Idea parsing failed: %s", e)

    # Fallback: create a basic task
    return {
        "agent": "code",
        "title": idea[:80],
        "description": idea,
        "priority": 3,
    }


# ── Task dispatcher ─────────────────────────────────────────────────────────

def get_agent(agent_name: str, project_root: Path):
    """Get an agent instance by name."""
    from jarvis.agents.code import CodeAgent
    from jarvis.agents.design import DesignAgent
    from jarvis.agents.qa import QAAgent
    from jarvis.agents.research import ResearchAgent
    from jarvis.agents.security import SecurityAgent

    agents = {
        "code": CodeAgent,
        "design": DesignAgent,
        "research": ResearchAgent,
        "qa": QAAgent,
        "security": SecurityAgent,
    }
    cls = agents.get(agent_name, CodeAgent)
    return cls(project_root)


def run_agent_on_task(task: dict, project_root: Path) -> dict:
    """Run the appropriate agent on a task. Returns the agent's result dict."""
    agent_name = task.get("agent", "code")
    try:
        agent = get_agent(agent_name, project_root)
        result = agent.execute(task)
        logger.info("Agent %s completed task %s: success=%s",
                    agent_name, task.get("id", "?"), result.get("success"))
        return result
    except Exception as e:
        logger.error("Agent %s crashed on task %s: %s",
                     agent_name, task.get("id", "?"), e, exc_info=True)
        return {
            "success": False,
            "result": f"Agent crashed: {e}",
            "commit_hash": None,
            "files_changed": [],
        }


def run_tests_and_maybe_revert(
    project_root: Path,
    commit_hash: str | None,
    *,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Run pytest after a commit. If tests fail, revert. Returns (success, output).

    Reverts are protected: they can be disabled entirely via the
    ``JARVIS_ALLOW_REVERT=0`` env var or by passing ``dry_run=True``, and they are
    skipped when the working tree has uncommitted changes (so a dirty tree is
    never clobbered).
    """
    if not commit_hash:
        # No commit was made, nothing to test
        return True, "No commit to test"

    try:
        result = subprocess.run(
            [".venv/bin/python", "-m", "pytest", "tests/", "-x", "--tb=short", "-q"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        success = result.returncode == 0
        output = result.stdout + result.stderr

        if not success:
            allow_revert = os.environ.get("JARVIS_ALLOW_REVERT", "1") != "0"
            if dry_run or not allow_revert:
                return False, f"Tests failed (revert disabled). Output: {output[-1000:]}"

            # Never revert over a dirty tree — an in-flight agent or manual edit
            # could be lost.
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if status.returncode == 0 and status.stdout.strip():
                return (
                    False,
                    f"Tests failed; revert skipped (working tree dirty). Output: {output[-1000:]}",
                )

            logger.warning("Tests failed after commit %s — reverting", commit_hash[:8])
            subprocess.run(
                ["git", "revert", "HEAD", "--no-edit"],
                cwd=project_root,
                capture_output=True,
                timeout=30,
                check=False,
            )
            return False, f"Tests failed, reverted. Output: {output[-1000:]}"

        return success, output[-1000:]
    except Exception as e:
        return False, f"Test runner error: {e}"

# ── Main Mayor loop ─────────────────────────────────────────────────────────

class Mayor:
    """The Mayor orchestrator. Runs the main loop and HTTP server."""

    def __init__(self, project_root: Path | None = None, port: int = MAYOR_PORT):
        self.project_root = project_root or PROJECT_ROOT
        self.port = port
        self.task_queue = TaskQueue()
        self.current_mode = get_mode()
        self.running = False
        self.last_mode_switch = None
        self.start_time = time.time()
        self._lock = threading.Lock()
        # Memory maintenance scheduling (idle loop)
        self._last_reindex = 0.0
        self._last_promote = 0.0
        self.MAINT_REINDEX_EVERY = 300    # 5 minutes
        self.MAINT_PROMOTE_EVERY = 21600  # 6 hours

    @property
    def uptime_seconds(self) -> float:
        """Return seconds since the Mayor started."""
        return time.time() - self.start_time

    def run_health_checks(self) -> dict[str, dict]:
        """Run health checks on all critical dependencies.
        
        Returns a dict of component -> {healthy: bool, detail: str, ...}
        """
        checks = {}

        # 1. Check Ollama connectivity
        try:
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/tags",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                models = data.get("models", [])
                checks["ollama"] = {
                    "healthy": True,
                    "detail": f"Ollama reachable at {OLLAMA_URL}, {len(models)} model(s) loaded",
                    "models": [m.get("name") for m in models[:5]],
                }
        except Exception as e:
            checks["ollama"] = {
                "healthy": False,
                "detail": f"Cannot reach Ollama at {OLLAMA_URL}: {e}",
                "error": str(e),
            }

        # 2. Check task queue
        try:
            stats = self.task_queue.stats()
            total = sum(stats.values()) if stats else 0
            pending = stats.get("pending_review", 0)
            checks["task_queue"] = {
                "healthy": True,
                "detail": f"Task queue accessible, {total} total task(s), {pending} pending review",
                "stats": stats,
            }
        except Exception as e:
            checks["task_queue"] = {
                "healthy": False,
                "detail": f"Task queue error: {e}",
                "error": str(e),
            }

        # 3. Check the project root exists and is writable
        try:
            root = self.project_root
            root_exists = root.exists()
            test_file = root / ".health_check_tmp"
            test_file.touch()
            test_file.unlink()
            checks["filesystem"] = {
                "healthy": root_exists,
                "detail": f"Project root {root} accessible and writable",
            }
        except Exception as e:
            checks["filesystem"] = {
                "healthy": False,
                "detail": f"Filesystem issue: {e}",
                "error": str(e),
            }

        # 4. Check running state
        checks["mayor"] = {
            "healthy": self.running,
            "detail": f"Mayor loop running for {self.uptime_seconds:.0f}s, mode={self.current_mode}",
            "mode": self.current_mode,
            "uptime_seconds": self.uptime_seconds,
        }

        return checks

    def submit_idea(self, idea: str, source: str = "user") -> dict:
        """Submit a raw idea. Parses it into a task and queues it."""
        with self._lock:
            logger.info("New idea from %s: %s", source, idea[:100])
            task_data = parse_idea(idea)
            if not task_data:
                task_data = {"agent": "code", "title": idea[:80], "description": idea, "priority": 3}

            task_id = self.task_queue.add_task(
                title=task_data["title"],
                description=task_data.get("description", ""),
                agent=task_data.get("agent", "code"),
                priority=task_data.get("priority", 3),
                source=source,
                raw_idea=idea,
            )
            logger.info("Queued task %s: [%s] %s", task_id, task_data.get("agent"), task_data["title"])
            return {
                "task_id": task_id,
                "agent": task_data.get("agent"),
                "title": task_data["title"],
                "status": "pending_review",
            }

    def check_mode_switch(self):
        """Check if we need to switch between coding and memory mode."""
        new_mode = get_mode()
        if new_mode != self.current_mode:
            logger.info("Switching mode: %s → %s", self.current_mode, new_mode)
            if new_mode == "coding":
                # Unload memory model, load coding model
                unload_model(MEMORY_MODEL)
                ensure_model_loaded(CODING_MODEL)
            else:
                # Unload coding model, load memory model
                unload_model(CODING_MODEL)
                ensure_model_loaded(MEMORY_MODEL)
                # Start Jarvis daemon for night mode
                self._start_night_mode()
            self.current_mode = new_mode
            self.last_mode_switch = datetime.now()

    def _start_night_mode(self):
        """Start the Jarvis memory daemon for night mode."""
        try:
            # Run a sync in the background. Open the log file through a context
            # manager so the parent's handle is closed after Popen spawns the
            # child (Popen dup's the fd into the child, so the child keeps
            # writing). This avoids leaking an fd on every night-mode switch.
            log_path = self.project_root / "logs" / "night_sync.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a") as log_file:
                subprocess.Popen(
                    [".venv/bin/python", "-m", "jarvis.cli", "sync", "--source", "all"],
                    cwd=self.project_root,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
            logger.info("Started night mode sync")
        except Exception as e:
            logger.error("Could not start night mode: %s", e)

    def auto_approve_old_tasks(self):
        """Auto-approve tasks that have been pending for too long."""
        now = datetime.now(timezone.utc)
        tasks = self.task_queue.list_tasks(status="pending_review")
        for task in tasks:
            created = task.get("created_at")
            if created:
                try:
                    created_dt = datetime.fromisoformat(created)
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=timezone.utc)
                    if (now - created_dt).total_seconds() > AUTO_APPROVE_TIMEOUT:
                        self.task_queue.approve_task(task["id"])
                        logger.info("Auto-approved task %s (timeout)", task["id"])
                except Exception:
                    pass

    def dispatch_next_task(self):
        """Get the next approved task and dispatch it to an agent."""
        if self.current_mode != "coding":
            return  # Only dispatch during coding mode

        task = self.task_queue.next_approved_task()
        if not task:
            return

        # Start the task
        if not self.task_queue.start_task(task["id"]):
            return

        logger.info("Dispatching task %s to %s agent: %s",
                     task["id"], task.get("agent"), task.get("title"))

        # Run the agent
        result = run_agent_on_task(task, self.project_root)

        # Check if the agent succeeded
        if result.get("success"):
            # Run tests after the commit
            test_ok, test_output = run_tests_and_maybe_revert(
                self.project_root, result.get("commit_hash")
            )
            if test_ok:
                self.task_queue.complete_task(
                    task["id"],
                    result=result.get("result", ""),
                    commit_hash=result.get("commit_hash", ""),
                )
                logger.info("Task %s completed successfully", task["id"])
            else:
                self.task_queue.fail_task(task["id"], error=f"Tests failed: {test_output[:200]}")
                logger.warning("Task %s failed (tests)", task["id"])
        else:
            self.task_queue.fail_task(task["id"], error=result.get("result", "Unknown error"))
            logger.warning("Task %s failed (agent)", task["id"])

    def _ollama_busy(self) -> bool:
        """True if Ollama is currently generating (avoid VRAM contention)."""
        try:
            req = urllib.request.Request(f"{OLLAMA_URL}/api/ps")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                for m in data.get("models", []):
                    if m.get("size_vram", 0) and not m.get("expires_at"):
                        return True
            return False
        except Exception:
            return False  # assume idle if we can't check

    def _maybe_idle_maintenance(self):
        """Run promote/reindex during idle instead of sleeping.

        Skips when there's an approved task queued, or when Ollama is
        generating right now (so maintenance never fights chat/agent for VRAM).
        """
        if self.task_queue.next_approved_task() is not None:
            return  # busy: leave it to the normal dispatch path
        now = time.time()
        if now - self._last_reindex >= self.MAINT_REINDEX_EVERY:
            if not self._ollama_busy():
                try:
                    from jarvis.maintenance import reindex_missing
                    done = reindex_missing(limit=200)
                    if done:
                        logger.info("Idle maintenance: re-indexed %d memory(-ies)", done)
                except Exception as exc:
                    logger.warning("Idle reindex failed: %s", exc)
                self._last_reindex = now
        if now - self._last_promote >= self.MAINT_PROMOTE_EVERY:
            if not self._ollama_busy():
                try:
                    from jarvis.maintenance import promote_old
                    promoted = promote_old(days=7, limit=500)
                    if promoted:
                        logger.info("Idle maintenance: promoted %d raw memory(-ies)", promoted)
                except Exception as exc:
                    logger.warning("Idle promote failed: %s", exc)
                self._last_promote = now

    def run_loop(self):
        """Main Mayor loop — runs forever."""
        logger.info("Mayor starting up. Project root: %s", self.project_root)
        logger.info("Mode: %s", self.current_mode)

        # Ensure the idea parser model is loaded
        ensure_model_loaded(IDEA_PARSER_MODEL)

        # If we're in coding mode, also load the coding model
        if self.current_mode == "coding":
            ensure_model_loaded(CODING_MODEL)
        else:
            ensure_model_loaded(MEMORY_MODEL)

        self.running = True
        while self.running:
            try:
                # Check mode switch
                self.check_mode_switch()

                # Auto-approve old tasks
                self.auto_approve_old_tasks()

                # Memory maintenance during idle (reindex/promote)
                self._maybe_idle_maintenance()

                # Dispatch next task (only in coding mode)
                self.dispatch_next_task()

            except Exception as e:
                logger.error("Mayor loop error: %s", e, exc_info=True)

            time.sleep(POLL_INTERVAL)

# ── HTTP API ─────────────────────────────────────────────────────────────────

_mayor_instance: Mayor | None = None


class MayorHTTPHandler(BaseHTTPRequestHandler):
    """HTTP handler for the Mayor API."""

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._json({}, 200)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/health":
            self._json({
                "status": "ok",
                "time": datetime.now().isoformat(),
                "version": "1.0.0",
                "uptime_seconds": _mayor_instance.uptime_seconds if _mayor_instance else 0,
            })
        elif path == "/health/check":
            """Enhanced health check — tests all critical dependencies."""
            if not _mayor_instance:
                self._json({"status": "error", "error": "mayor not initialized"}, 500)
                return
            checks = _mayor_instance.run_health_checks()
            overall = "ok" if all(c.get("healthy", False) for c in checks.values()) else "degraded"
            self._json({
                "status": overall,
                "time": datetime.now().isoformat(),
                "checks": checks,
                "mode": _mayor_instance.current_mode,
                "running": _mayor_instance.running,
            })
        elif path == "/health/alert":
            """Return alert payload if system is degraded, for monitoring scripts."""
            if not _mayor_instance:
                self._json({"alert": "mayor not initialized", "severity": "critical"}, 500)
                return
            checks = _mayor_instance.run_health_checks()
            unhealthy = {k: v for k, v in checks.items() if not v.get("healthy", False)}
            if unhealthy:
                self._json({
                    "alert": "Jarvis system degraded",
                    "severity": "warning",
                    "unhealthy_components": unhealthy,
                    "time": datetime.now().isoformat(),
                })
            else:
                self._json({
                    "alert": None,
                    "severity": "ok",
                    "time": datetime.now().isoformat(),
                })
        elif path == "/status":
            if not _mayor_instance:
                self._json({"error": "mayor not initialized"}, 500)
                return
            self._json({
                "mode": _mayor_instance.current_mode,
                "project_root": str(_mayor_instance.project_root),
                "queue_stats": _mayor_instance.task_queue.stats(),
                "running": _mayor_instance.running,
            })
        elif path == "/tasks":
            if not _mayor_instance:
                self._json({"error": "mayor not initialized"}, 500)
                return
            status = qs.get("status", [None])[0]
            tasks = _mayor_instance.task_queue.list_tasks(status=status, limit=50)
            self._json({"tasks": tasks})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        global _mayor_instance
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        qs = parse_qs(parsed.query)

        # Read body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length else ""

        if path == "/idea":
            if not _mayor_instance:
                self._json({"error": "mayor not initialized"}, 500)
                return
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                data = {"idea": body}
            idea = data.get("idea", "") or body
            source = data.get("source", "http")
            if not idea:
                self._json({"error": "no idea provided"}, 400)
                return
            try:
                result = _mayor_instance.submit_idea(idea, source=source)
                self._json(result)
            except Exception as e:
                logger.error("Idea submission failed: %s", e, exc_info=True)
                self._json({"error": f"idea processing failed: {e}"}, 500)

        elif path == "/tasks/approve":
            if not _mayor_instance:
                self._json({"error": "mayor not initialized"}, 500)
                return
            task_id = qs.get("id", [None])[0]
            approve_all = qs.get("all", ["false"])[0].lower() == "true"
            if approve_all:
                count = _mayor_instance.task_queue.approve_all()
                self._json({"approved": count})
            elif task_id:
                ok = _mayor_instance.task_queue.approve_task(task_id)
                self._json({"success": ok, "task_id": task_id})
            else:
                self._json({"error": "provide ?id= or ?all=true"}, 400)

        elif path == "/tasks/reject":
            if not _mayor_instance:
                self._json({"error": "mayor not initialized"}, 500)
                return
            task_id = qs.get("id", [None])[0]
            if task_id:
                ok = _mayor_instance.task_queue.reject_task(task_id)
                self._json({"success": ok, "task_id": task_id})
            else:
                self._json({"error": "provide ?id="}, 400)
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, format, *args):
        # Suppress default logging (we use our own)
        pass


def run_mayor(port: int = MAYOR_PORT, project_root: Path | str | None = None):
    """Start the Mayor daemon with HTTP server + main loop."""
    global _mayor_instance

    root = Path(project_root) if project_root else PROJECT_ROOT
    _mayor_instance = Mayor(project_root=root, port=port)

    # Start the main loop in a background thread
    loop_thread = threading.Thread(target=_mayor_instance.run_loop, daemon=True)
    loop_thread.start()

    # Start the HTTP server in the main thread
    server = HTTPServer(("0.0.0.0", port), MayorHTTPHandler)
    logger.info("Mayor HTTP API on http://0.0.0.0:%d", port)
    logger.info("Submit ideas: POST http://100.102.0.99:%d/idea", port)
    logger.info("Status:       GET  http://100.102.0.99:%d/status", port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Mayor shutting down...")
        _mayor_instance.running = False
        server.shutdown()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Jarvis Mayor orchestrator")
    parser.add_argument("--port", type=int, default=MAYOR_PORT)
    parser.add_argument("--root", type=str, default=str(PROJECT_ROOT))
    args = parser.parse_args()
    run_mayor(port=args.port, project_root=args.root)
