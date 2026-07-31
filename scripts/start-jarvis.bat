@echo off
REM ============================================================
REM start-jarvis.bat — Auto-start Jarvis Mayor/Dashboard on boot
REM
REM This script is launched by Windows Task Scheduler on startup.
REM It activates the Jarvis venv, starts the dashboard (which
REM runs the Mayor background loop + FastAPI uvicorn server on
REM port 8766), and logs output for troubleshooting.
REM
REM Add via Task Scheduler:
REM   Trigger: At startup
REM   Action:  Start a program -> C:\Users\despo\jarvis\scripts\start-jarvis.bat
REM   User:    despo
REM ============================================================
setlocal

set "JARVIS_ROOT=C:\Users\despo\jarvis"
set "LOG_DIR=%JARVIS_ROOT%\logs"
set "LOG_FILE=%LOG_DIR%\dashboard.log"
set "VENV_PYTHON=%JARVIS_ROOT%\.venv\Scripts\python.exe"

REM Ensure log directory exists
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [%date% %time%] Starting Jarvis Dashboard... >> "%LOG_FILE%"

REM Wait for Ollama to be ready (up to 60 seconds)
set "OLLAMA_READY="
for /l %%i in (1,1,30) do (
    curl -s http://100.102.0.99:11434/api/tags >nul 2>&1
    if not errorlevel 1 (
        set "OLLAMA_READY=1"
        echo [%date% %time%] Ollama is ready >> "%LOG_FILE%"
        goto :ollama_ok
    )
    timeout /t 2 /nobreak >nul
)
echo [%date% %time%] WARNING: Ollama did not respond within 60s, starting anyway >> "%LOG_FILE%"
:ollama_ok

REM Kill any existing dashboard process
for /f "tokens=2" %%a in ('tasklist /fi "imagename eq python.exe" /fo list ^| findstr /i "8766"') do (
    taskkill /f /pid %%a >nul 2>&1
)

REM Set environment variables for the Jarvis process
set "OLLAMA_HOST=100.102.0.99"
set "OLLAMA_PORT=11434"
set "JARVIS_ROOT=%JARVIS_ROOT%"
set "MAYOR_PORT=8767"

REM Start the dashboard (which also starts the Mayor background loop)
cd /d "%JARVIS_ROOT%"
echo [%date% %time%] Starting jarvis dashboard on port 8766... >> "%LOG_FILE%"
"%VENV_PYTHON%" -m jarvis.cli dashboard --port 8766 >> "%LOG_FILE%" 2>&1

REM If we get here, the process exited
echo [%date% %time%] Dashboard exited with code %errorlevel% >> "%LOG_FILE%"
exit /b %errorlevel%