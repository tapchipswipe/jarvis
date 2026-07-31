"""
jarvis/agents/base.py — Base Agent class for all coding agents.

Each agent:
1. Reads a task from the queue
2. Builds a prompt with file contents + task description
3. Calls Ollama via HTTP API
4. Parses the response and writes changes to the codebase
5. Commits to the bot/ branch
6. Runs pytest; if tests fail, the Mayor reverts
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

import logging

logger = logging.getLogger("jarvis.agents")

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "100.102.0.99")
OLLAMA_PORT = os.environ.get("OLLAMA_PORT", "11434")
OLLAMA_URL = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}"


class BaseAgent:
    """Base class for all coding agents."""

    name: str = "base"
    model: str = "llama3.2:1b"
    description: str = "Base agent — does nothing specific"

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root)

    # ── Ollama HTTP API ──────────────────────────────────────────────────────

    def call_llm(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        temperature: float = 0.3,
        keep_alive: str = "30m",
    ) -> str:
        """Call Ollama /api/generate and return the response text."""
        use_model = model or self.model
        payload = {
            "model": use_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
            "keep_alive": keep_alive,
        }
        if system:
            payload["system"] = system

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read().decode())
                return result.get("response", "")
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
            logger.error("Ollama call failed: %s", e)
            return f"[ERROR: LLM call failed: {e}]"

    # ── File operations ──────────────────────────────────────────────────────

    def read_file(self, relative_path: str) -> str:
        """Read a file from the project root."""
        full_path = self.project_root / relative_path
        try:
            return full_path.read_text(encoding="utf-8", errors="ignore")
        except FileNotFoundError:
            return f"[File not found: {relative_path}]"
        except Exception as e:
            return f"[Error reading {relative_path}: {e}]"

    def write_file(self, relative_path: str, content: str) -> bool:
        """Write a file in the project root. Creates parent dirs."""
        full_path = self.project_root / relative_path
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            return True
        except Exception as e:
            logger.error("Failed to write %s: %s", relative_path, e)
            return False

    def list_files(self, pattern: str = "**/*.py") -> list[str]:
        """List files matching a glob pattern."""
        return [
            str(p.relative_to(self.project_root))
            for p in self.project_root.glob(pattern)
            if "__pycache__" not in str(p) and ".venv" not in str(p)
        ]

    # ── Git operations ───────────────────────────────────────────────────────

    def git_commit(self, message: str, files: list[str] | None = None) -> str | None:
        """Stage and commit files to the bot branch. Returns commit hash."""
        try:
            # Ensure we're on the bot branch
            subprocess.run(
                ["git", "checkout", "bot"],
                cwd=self.project_root,
                capture_output=True,
                timeout=10,
            )
            # Stage files
            if files:
                for f in files:
                    subprocess.run(
                        ["git", "add", f],
                        cwd=self.project_root,
                        capture_output=True,
                        timeout=10,
                    )
            else:
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=self.project_root,
                    capture_output=True,
                    timeout=10,
                )
            # Check if there's anything to commit
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if not status.stdout.strip():
                logger.info("No changes to commit")
                return None
            # Commit
            commit = subprocess.run(
                ["git", "commit", "-m", f"[{self.name}] {message}"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if commit.returncode != 0:
                logger.error("Git commit failed: %s", commit.stderr)
                return None
            # Get commit hash
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return hash_result.stdout.strip()
        except Exception as e:
            logger.error("Git operation failed: %s", e)
            return None

    def git_revert_last(self) -> bool:
        """Revert the last commit (used when tests fail)."""
        try:
            result = subprocess.run(
                ["git", "revert", "HEAD", "--no-edit"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode == 0
        except Exception as e:
            logger.error("Git revert failed: %s", e)
            return False

    # ── Test runner ───────────────────────────────────────────────────────────

    def run_tests(self) -> tuple[bool, str]:
        """Run pytest and return (success, output)."""
        try:
            result = subprocess.run(
                [".venv/bin/python", "-m", "pytest", "tests/", "-x", "--tb=short", "-q"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=120,
            )
            success = result.returncode == 0
            output = result.stdout + result.stderr
            return success, output
        except Exception as e:
            return False, str(e)

    # ── Task execution (override in subclasses) ──────────────────────────────

    def execute(self, task: dict) -> dict:
        """Execute a task. Override in subclasses.
        Returns a dict with 'success', 'result', 'commit_hash', 'files_changed'."""
        raise NotImplementedError("Subclasses must implement execute()")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def extract_code_blocks(self, text: str) -> list[str]:
        """Extract code from markdown code blocks in LLM response."""
        blocks = re.findall(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
        return blocks

    def extract_json(self, text: str) -> dict | None:
        """Try to extract a JSON object from LLM response text."""
        # Try to find a JSON block
        json_match = re.search(r"```(?:json)?\n(.*?)```", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        # Try to parse the whole text as JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Try to find a JSON object anywhere in the text
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass
        return None
