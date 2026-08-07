"""
jarvis/agents/research.py — Chief Research agent.

Reads the codebase, writes architecture docs, plans features.
Uses deepseek-r1:14b for reasoning.
"""
from __future__ import annotations

import logging

from jarvis.agents.base import BaseAgent

logger = logging.getLogger("jarvis.agents.research")

SYSTEM_PROMPT = """You are Chief Research, an expert software architect.
You analyze codebases, write architecture documentation, and plan features.

Rules:
- Be concise but thorough
- Output markdown documentation
- When planning a feature, break it into concrete steps
- Reference actual file paths from the codebase
"""


class ResearchAgent(BaseAgent):
    name = "research"
    model = "qwen2.5:7b-instruct-q4_K_M"
    description = "Architecture docs, codebase analysis, feature planning"

    def execute(self, task: dict) -> dict:
        title = task.get("title", "")
        description = task.get("description", "")

        # List all Python files for context
        all_files = self.list_files("**/*.py")
        file_list = "\n".join(f"  {f}" for f in all_files[:40])

        # Read AGENTS.md if it exists
        agents_md = self.read_file("AGENTS.md")

        prompt = f"""TASK: {title}

DESCRIPTION: {description}

PROJECT FILES:
{file_list}

AGENTS.md:
{agents_md[:2000]}

Analyze and write documentation or a plan for this task.
Output as markdown."""

        response = self.call_llm(prompt, system=SYSTEM_PROMPT)

        if response.startswith("[ERROR"):
            return {"success": False, "result": response, "commit_hash": None, "files_changed": []}

        # Write the research output to docs/
        safe_name = title.lower().replace(" ", "_").replace("-", "_")[:40]
        safe_name = "".join(c for c in safe_name if c.isalnum() or c == "_")
        doc_path = f"docs/{safe_name}.md"

        if self.write_file(doc_path, f"# {title}\n\n{response}"):
            commit_hash = self.git_commit(title, files=[doc_path])
            return {
                "success": True,
                "result": f"Wrote {doc_path}",
                "commit_hash": commit_hash,
                "files_changed": [doc_path],
            }

        return {"success": False, "result": "Failed to write doc", "commit_hash": None, "files_changed": []}
