# VoiceOps

An AI voice agent for customer support, with a dashboard for managing conversations, orders and tickets.

## About

VoiceOps lets customers talk to a support agent in the browser. The agent transcribes speech, answers policy questions from a knowledge base, looks up and updates orders through validated backend tools, and replies with synthesized speech. Sensitive actions require customer confirmation, and the agent escalates to a human when needed. Every conversation is stored with its transcript and tool calls so support staff can review it in the dashboard.

## Features

- Voice conversations over WebSocket with speech-to-text and text-to-speech
- Knowledge base answers using retrieval (RAG) over policy and FAQ documents
- Order status, delivery estimates, cancellations and return requests
- Confirmation required before cancelling orders, creating returns or updating customer details
- Escalation to a human with an automatically created support ticket
- Offline fallback when no LLM is configured
- Dashboard for conversations, customers, orders, tickets, knowledge base and analytics
- JWT authentication; self-registered users get read-only access with masked customer data

## Tech Stack

- **Frontend:** React, Vite, React Router, Recharts
- **Backend:** FastAPI, SQLAlchemy (async), PostgreSQL
- **AI:** OpenAI-compatible LLM API (Ollama, OpenAI, Groq), Whisper (speech-to-text), Edge TTS / OpenAI TTS, sentence-transformers (embeddings)
- **Deployment:** Docker Compose, Nginx

## Project Structure

```
backend/
  app/api/routes/       REST and WebSocket endpoints
  app/services/         Agent, LLM, RAG, voice and tool logic
  app/db/               Database models and seed data
  scripts/seed.py       Reset the database to demo data
  tests/                Test suite
frontend/
  src/pages/            Dashboard and voice agent pages
  src/voice/            Browser audio capture and playback
data/knowledge_base/    Policy and FAQ documents used by the agent
docker-compose.yml      PostgreSQL, backend, frontend and optional Ollama
.env.example            Configuration template
```

## Setup

### Docker

```bash
cp .env.example .env
docker compose up --build
```

The app runs at http://localhost:3000.

To run a local LLM with Ollama, set `LLM_BASE_URL=http://ollama:11434/v1` in `.env`, then:

```bash
docker compose --profile local-llm up --build
docker compose exec ollama ollama pull qwen2.5:3b
```

### Local

Requirements: Python 3.12+, Node 20+, PostgreSQL.

```bash
# Backend
cp .env.example backend/.env    # set DATABASE_URL and JWT_SECRET
cd backend
python -m venv venv
venv\Scripts\activate           # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

```bash
# Frontend
cd frontend
npm install
npm run dev
```

The app runs at http://localhost:5173. Tables are created and demo data is seeded on first start.

### Tests

```bash
cd backend
pip install -r requirements-dev.txt
pytest
```

## Usage

1. Sign in with the demo account `admin@voiceops.ai` / `admin123`.
2. Open **Voice Agent** and start a conversation.
3. Ask things like "What's the status of order ORD001?" or "How long do refunds take?"
4. Review the conversation, tool calls and metrics under **Conversations** and **Analytics**.

## API

All endpoints are under `/api`. Interactive docs are available at http://localhost:8000/docs.

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/auth/login` | Sign in and get a token |
| POST | `/api/conversations` | Start a conversation |
| POST | `/api/conversations/{id}/messages` | Send a text message to the agent |
| WS | `/api/voice/ws/{conversation_id}` | Real-time voice conversation |
| POST | `/api/voice/transcribe` | Speech to text |
| POST | `/api/voice/synthesize` | Text to speech |
| GET | `/api/orders` | List orders |
| GET | `/api/tickets` | List support tickets |
| GET | `/api/knowledge/search` | Search the knowledge base |
| GET | `/api/analytics/overview` | Dashboard metrics |
| GET | `/api/health` | Health check |
