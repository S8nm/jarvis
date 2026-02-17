# J.A.R.V.I.S. Protocol

**Just A Rather Very Intelligent System** — A local, always-on, multimodal AI assistant.

## Features (Phase 2)
- **Voice Interaction**: Wake word ("Hey Jarvis"), STT (Whisper), TTS (Piper).
- **Mental Notes**: Add, list, search, and delete notes with tags.
- **Calendar Management**: Create events, check schedule, and export to ICS.
- **Vision Integration**: "Jarvis, look at this" activates the camera to analyze what's visible.
- **File & Script Operations**: Read/write files in sandbox, generate python scripts, and execute them.
- **Dashboard**: Real-time visualization of notes, calendar events, and system status.

## Quick Start

### Prerequisites
- Python 3.12+
- Node.js 22+
- Ollama (running locally)
- Models: `llama3.1:8b` (chat), `llava:13b` (vision)

### 1. Pull an LLM model
```bash
ollama pull llama3.1:8b
```

### 2. Start Ollama
```bash
ollama serve
```

### 3. Start the Backend
```bash
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

### 4. Start the Frontend
```bash
cd frontend
npm install
npm run dev
```

### 5. Launch Electron (optional — for fullscreen on monitor #4)
```bash
cd frontend
npm run electron:dev
```

## Architecture

```
User Voice → Wake Word (openWakeWord) → STT (faster-whisper) → LLM (Ollama) → TTS (Piper) → Speaker
                                    ↕ WebSocket ↕
                              Electron Dashboard (React)
```

## Project Structure

- `backend/` — Python FastAPI server, agent, speech, LLM
- `frontend/` — Electron + Vite + React dashboard
- `models/` — Downloaded model files
- `data/` — SQLite databases, notes
- `sandbox/` — Generated files/scripts
