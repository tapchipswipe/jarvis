@echo off
REM ============================================================
REM resume.bat — "resume the jarvis project" on Lightspeed.
REM
REM From a Lightspeed terminal just run:   resume.bat
REM (or launch cline interactively and type:  "resume the jarvis project"
REM  — cline reads AGENTS.md + docs/RESUME.md at the repo root automatically).
REM
REM Pulls latest context first (so AGENTS/docs/RESUME are current), then asks
REM the headless cline CLI to resume with full memory + context access.
REM ============================================================
cd /d C:\Users\despo\jarvis
git pull --quiet --ff-only 2>nul
cline --json "resume the jarvis project"
