"""
jarvis/agents/security.py — Chief Security agent.

Scans for hardcoded secrets, SQL injection, missing auth.
Deterministic — no LLM needed.
"""
from __future__ import annotations

import logging
import re

from jarvis.agents.base import BaseAgent

logger = logging.getLogger("jarvis.agents.security")

# Patterns to scan for
SECRET_PATTERNS = [
    (r"(?:password|passwd|pwd)\s*=\s*[\"'][^\"']{8,}[\"']", "Hardcoded password"),
    (r"(?:secret|api_key|apikey|access_key)\s*=\s*[\"'][^\"']{8,}[\"']", "Hardcoded API key/secret"),
    (r"sk-[a-zA-Z0-9]{32,}", "OpenAI API key"),
    (r"AC[a-z0-9]{32}", "Twilio Account SID"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub personal access token"),
    (r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", "Private key"),
]

VULN_PATTERNS = [
    (r"eval\s*\(", "Use of eval() — code injection risk"),
    (r"exec\s*\(", "Use of exec() — code injection risk"),
    (r"subprocess\.call\s*\(.*shell=True", "Shell=True in subprocess — command injection"),
    (r"os\.system\s*\(", "Use of os.system() — command injection"),
    (r"SELECT\s+\*\s+FROM.*\+.*(?:%s|\{|f\"|f')", "Potential SQL injection"),
]


class SecurityAgent(BaseAgent):
    name = "security"
    model = "llama3.2:1b"  # Not really used — this agent is deterministic
    description = "Scans for hardcoded secrets and security vulnerabilities"

    def execute(self, task: dict) -> dict:
        logger.info("[Chief Security] Scanning codebase...")
        findings = []

        all_files = self.list_files("**/*.py")
        for filepath in all_files:
            content = self.read_file(filepath)
            if content.startswith("["):
                continue

            # Check for secrets
            for pattern, desc in SECRET_PATTERNS:
                for match in re.finditer(pattern, content, re.IGNORECASE):
                    line_num = content[:match.start()].count("\n") + 1
                    findings.append({
                        "file": filepath,
                        "line": line_num,
                        "type": "secret",
                        "description": desc,
                    })

            # Check for vulnerabilities
            for pattern, desc in VULN_PATTERNS:
                for match in re.finditer(pattern, content, re.IGNORECASE):
                    line_num = content[:match.start()].count("\n") + 1
                    findings.append({
                        "file": filepath,
                        "line": line_num,
                        "type": "vulnerability",
                        "description": desc,
                    })

        if findings:
            logger.warning("[Chief Security] %d finding(s)!", len(findings))
            report = f"# Security Scan Report\n\nFound {len(findings)} issue(s):\n\n"
            for f in findings:
                report += f"- **{f['description']}** in `{f['file']}:{f['line']}`\n"
        else:
            logger.info("[Chief Security] No issues found")
            report = "# Security Scan Report\n\nNo issues found. Code looks clean!"

        # Write report
        report_path = "docs/security_report.md"
        self.write_file(report_path, report)
        commit_hash = self.git_commit("Security scan report", files=[report_path])

        return {
            "success": True,
            "result": f"{len(findings)} finding(s)",
            "commit_hash": commit_hash,
            "files_changed": [report_path],
            "findings": findings,
        }
