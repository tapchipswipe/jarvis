"""
jarvis/agents/design.py — Chief Design agent.

Handles UI/UX improvements, dashboard CSS, visualizations.
"""
from __future__ import annotations

import logging

from jarvis.agents.base import BaseAgent

logger = logging.getLogger("jarvis.agents.design")

SYSTEM_PROMPT = """You are Chief Design, an expert UI/UX designer for the Jarvis dashboard.
The dashboard is a FastAPI app with inline HTML/CSS and HTMX for interactivity.
It uses a dark theme with #0f1117 background.

Rules:
- Output complete HTML/CSS/Python code in markdown blocks
- For dashboard changes, modify jarvis/dashboard.py
- Use modern CSS (flexbox, grid, CSS variables)
- Keep it lightweight — no heavy frameworks, just vanilla CSS + HTMX
- Add # FILE: jarvis/dashboard.py to code blocks
"""


class DesignAgent(BaseAgent):
    name = "design"
    model = "qwen2.5:7b-instruct-q4_K_M"
    description = "Dashboard UI/UX improvements, CSS, visualizations"

    def execute(self, task: dict) -> dict:
        title = task.get("title", "")
        description = task.get("description", "")
        raw_idea = task.get("raw_idea", "")

        # Read the current dashboard
        dashboard_code = self.read_file("jarvis/dashboard.py")

        prompt = f"""TASK: {title}

DESCRIPTION: {description}

ORIGINAL IDEA: {raw_idea}

CURRENT DASHBOARD CODE (first 4000 chars):
{dashboard_code[:4000]}

Improve the dashboard based on this task. Output the complete modified file."""

        response = self.call_llm(prompt, system=SYSTEM_PROMPT)

        if response.startswith("[ERROR"):
            return {"success": False, "result": response, "commit_hash": None, "files_changed": []}

        code_blocks = self.extract_code_blocks(response)
        files_changed = []

        for block in code_blocks:
            file_match = None
            for line in block.split("\n"):
                if line.strip().startswith("# FILE:"):
                    file_match = line.replace("# FILE:", "").strip()
                    break
            if file_match:
                lines = [l for l in block.split("\n") if not l.strip().startswith("# FILE:")]
                content = "\n".join(lines).strip()
                if self.write_file(file_match, content):
                    files_changed.append(file_match)

        commit_hash = self.git_commit(title, files=files_changed) if files_changed else None

        return {
            "success": len(files_changed) > 0,
            "result": f"Modified {len(files_changed)} file(s)",
            "commit_hash": commit_hash,
            "files_changed": files_changed,
        }
