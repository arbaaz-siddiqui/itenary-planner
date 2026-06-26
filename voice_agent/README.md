# Voice Agent POC — Pipecat WebSocket

Local voice pipeline: Groq STT → Llama 3.1 8B → Sarvam TTS (bulbul:v3, Ishita)

## Setup

```bash
cd voice_agent
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

## Run

```bash
venv\Scripts\uvicorn server:app --port 8765 --ws wsproto
```

Then open `index.html` in Chrome and click the call button.

## Env vars needed (in root `.env`)

- `GROQ_API_KEY` — for STT (Whisper) + LLM (Llama)
- `SARWAM_AI_API_KEY` — for Sarvam TTS
