"""
jarvis/agents/code.py — Chief Code agent.

Reads a task, finds relevant files, builds a prompt, calls qwen2.5-coder:14b,
and writes the generated code back to the codebase.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from jarvis.agents.base import BaseAgent

logger = logging.getLogger("jarvis.agents.code")

SYSTEM_PROMPT = """You are Chief Code, an expert Python developer working on the Jarvis project.
Jarvis is a local ambient memory agent that collects data from macOS sources,
stores it in ChromaDB + SQLite, and provides chat/search/trigger capabilities.

Rules:
- Write clean, working Python code that follows existing conventions
- Use only stdlib + existing dependencies (chromadb, click, fastapi, uvicorn)
- Keep files under 300 lines unless unavoidable
- Add docstrings to new functions/classes
- Handle errors gracefully — never crash the daemon
- Output your code in markdown code blocks with ```python
- If you need to modify an existing file, output the COMPLETE file content
- If creating a new file, specify the path in a comment like: # FILE: jarvis/new_module.py
"""


class CodeAgent(BaseAgent):
    name = "code"
    model = "qwen2.5-coder:14b"
    description = "Writes features, fixes bugs, refactors code"

    def find_relevant_files(self, task: dict) -> list[str]:
        """Find files relevant to the task based on keywords in the description."""
        text = (task.get("title", "") + " " + task.get("description", "")).lower()
        all_files = self.list_files("**/*.py")
        relevant = []
        keywords = self._extract_keywords(text)
        for f in all_files:
            # Check if filename matches any keyword
            fname = f.lower()
            if any(kw in fname for kw in keywords):
                relevant.append(f)
        # Also include core files always
        core_files = ["jarvis/store.py", "jarvis/cli.py", "jarvis/brain.py",
                      "jarvis/agent.py", "jarvis/tools.py", "jarvis/dashboard.py"]
        for cf in core_files:
            if cf not in relevant and (self.project_root / cf).exists():
                relevant.append(cf)
        return relevant[:10]  # Limit context size

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract meaningful keywords from the task text."""
        stop_words = {"the", "a", "an", "to", "for", "and", "or", "in", "on",
                      "at", "is", "are", "was", "were", "be", "been", "have",
                      "has", "had", "do", "does", "did", "will", "would", "should",
                      "could", "may", "might", "must", "shall", "can", "need",
                      "make", "add", "fix", "implement", "create", "update"}
        words = [w.strip(".,!?;:()[]{}'\"") for w in text.split()]
        keywords = [w for w in words if len(w) > 2 and w.lower() not in stop_words]
        return keywords

    def execute(self, task: dict) -> dict:
        """Execute a coding task."""
        title = task.get("title", "Untitled task")
        description = task.get("description", "")
        raw_idea = task.get("raw_idea", "")

        logger.info("[Chief Code] Starting task: %s", title)

        # Find relevant files
        relevant_files = self.find_relevant_files(task)

        # Build context from relevant files
        file_context = ""
        for f in relevant_files[:5]:  # Limit to 5 files for context
            content = self.read_file(f)
            if not content.startswith("["):
                file_context += f"\n--- FILE: {f} ---\n{content[:3000]}\n"

        # Build the prompt
        prompt = f"""TASK: {title}

DESCRIPTION: {description}

ORIGINAL IDEA: {raw_idea}

RELEVANT FILES IN THE PROJECT:
{file_context}

Implement this task. If modifying an existing file, output the complete file.
If creating a new file, add a comment line: # FILE: path/to/file.py
Write clean, working Python code."""

        # Call the LLM
        response = self.call_llm(prompt, system=SYSTEM_PROMPT)

        if response.startswith("[ERROR"):
            return {
                "success": False,
                "result": response,
                "commit_hash": None,
                "files_changed": [],
            }

        # Parse the response and extract code blocks
        code_blocks = self.extract_code_blocks(response)
        files_changed = []

        for block in code_blocks:
            # Check if this block has a FILE: directive
            file_match = None
            for line in block.split("\n"):
                if line.strip().startswith("# FILE:"):
                    file_match = line.replace("# FILE:", "").strip()
                    break

            if file_match:
                # New file — strip the FILE: comment line
                lines = block.split("\n")
                content_lines = [l for l in lines if not l.strip().startswith("# FILE:")]
                content = "\n".join(content_lines).strip()
                if self.write_file(file_match, content):
                    files_changed.append(file_match)
                    logger.info("[Chief Code] Created/updated: %s", file_match)

        # If no FILE: directives, try to figure out what file to write
        if not files_changed and code_blocks:
            # Write the first code block to a file based on the task title
            safe_name = title.lower().replace(" ", "_").replace("-", "_")[:30]
            # Clean up non-alphanumeric
            safe_name = "".join(c for c in safe_name if c.isalnum() or c == "_")
            if safe_name:
                new_file = f"jarvis/agents/generated_{safe_name}.py"
                if self.write_file(new_file, code_blocks[0]):
                    files_changed.append(new_file)
                    logger.info("[Chief Code] Created generated file: %s", new_file)

        if not files_changed:
            logger.warning("[Chief Code] No code blocks extracted from response")
            return {
                "success": False,
                "result": "No code blocks found in LLM response",
                "commit_hash": None,
                "files_changed": [],
            }

        # Commit to bot branch
        commit_hash = self.git_commit(title, files=files_changed)

        return {
            "success": commit_hash is not None,
            "result": f"Modified {len(files_changed)} file(s): {', '.join(files_changed)}",
            "commit_hash": commit_hash,
            "files_changed": files_changed,
        }
