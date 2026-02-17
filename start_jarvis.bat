@echo off
echo ═══════════════════════════════════════════
echo   J.A.R.V.I.S. Protocol — Starting Up
echo ═══════════════════════════════════════════
echo.

REM Set HF_TOKEN in your environment or .env file
if not defined HF_TOKEN set HF_TOKEN=your_huggingface_token_here
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
set NO_TORCH_COMPILE=1
set NO_CUDA_GRAPH=1
REM Clear VS Code / Claude Code env that breaks Electron
set ELECTRON_RUN_AS_NODE=

REM Check Ollama
echo [1/5] Checking Ollama...
where ollama >nul 2>&1
if %errorlevel% neq 0 (
    echo  ⚠  Ollama not installed - LLM will use online fallback
    echo     Download from: https://ollama.com/download
) else (
    ollama list >nul 2>&1
    if %errorlevel% neq 0 (
        echo  ⚠  Ollama not running. Starting it...
        start "Ollama" ollama serve
        timeout /t 5 >nul
    )
    echo  ✓ Ollama ready
)

REM Start PersonaPlex Server
echo [2/5] Starting PersonaPlex Server (port 8998)...
set PERSONAPLEX_DIR=%~dp0..\personaplex
set PERSONAPLEX_VENV=%PERSONAPLEX_DIR%\venv\Scripts
set SSL_DIR=%PERSONAPLEX_DIR%\ssl_certs
if exist "%PERSONAPLEX_VENV%\python.exe" (
    start "PersonaPlex Server" cmd /k "%PERSONAPLEX_VENV%\python.exe -m moshi.server --host localhost --port 8998 --ssl %SSL_DIR%"
    echo  ✓ PersonaPlex starting (model load may take 30-60s)
    timeout /t 10 >nul
) else (
    echo  ⚠  PersonaPlex not installed — voice will run in text-only mode
)

REM Start Backend
echo [3/5] Starting Backend...
cd /d "%~dp0backend"
if not exist "venv" (
    echo  → Creating virtual environment...
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)
start "JARVIS Backend" cmd /k "call venv\Scripts\activate.bat && python main.py"
cd /d "%~dp0"
timeout /t 3 >nul
echo  ✓ Backend started (includes Bridge Proxy on port 8999)

REM Start Frontend + Electron
echo [4/5] Starting Frontend + Electron...
cd /d "%~dp0frontend"
if not exist "node_modules" (
    echo  → Installing dependencies...
    call npm install --legacy-peer-deps
)
start "JARVIS Frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev"
echo  ✓ Vite dev server starting...
timeout /t 5 >nul

echo [5/5] Launching Electron window...
start "JARVIS Electron" cmd /k "set ELECTRON_RUN_AS_NODE= && cd /d "%~dp0frontend" && npx wait-on http://localhost:5173 && npx electron ."
cd /d "%~dp0"

echo.
echo ═══════════════════════════════════════════
echo   J.A.R.V.I.S. is online, sir.
echo ═══════════════════════════════════════════
echo.
echo   Backend:     http://127.0.0.1:8765
echo   Bridge:      ws://localhost:8999/api/chat
echo   PersonaPlex: https://localhost:8998
echo   Frontend:    http://localhost:5173
echo.
pause
