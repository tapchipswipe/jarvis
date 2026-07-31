"""
jarvis/agents/qa.py — Chief QA agent.

Runs pytest, parses failures, and reports back. If tests fail,
creates fix tasks for Chief Code.
"""
from __future__ import annotations

import logging
import re

from jarvis.agents.base import BaseAgent

logger = logging.getLogger("jarvis.agents.qa")


class QAAgent(BaseAgent):
    name = "qa"
    model = "llama3.2:1b"  # QA is mostly deterministic; small model for parsing
    description = "Runs tests, verifies code quality, reports failures"

    def execute(self, task: dict) -> dict:
        """Run tests and report results."""
        logger.info("[Chief QA] Running tests...")

        success, output = self.run_tests()

        if success:
            logger.info("[Chief QA] All tests passed!")
            return {
                "success": True,
                "result": "All tests passed",
                "commit_hash": None,
                "files_changed": [],
                "test_output": output[-2000:],
            }

        # Parse test failures
        failures = self._parse_failures(output)
        logger.warning("[Chief QA] %d test failure(s) detected", len(failures))

        return {
            "success": False,
            "result": f"{len(failures)} test failure(s): {failures[:5]}",
            "commit_hash": None,
            "files_changed": [],
            "test_output": output[-2000:],
            "failures": failures,
        }

    def _parse_failures(self, output: str) -> list[str]:
        """Parse pytest output for failure descriptions."""
        failures = []
        # Match lines like: FAILED tests/test_foo.py::test_bar - reason
        for match in re.finditer(r"FAILED (.+)", output):
            failures.append(match.group(1).strip())
        # Also match assertion errors
        for match in re.finditer(r"AssertionError: (.+)", output):
            failures.append(f"AssertionError: {match.group(1).strip()}")
        return failures
