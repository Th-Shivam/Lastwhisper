# LastWhisper

A calm, offline-first voice memory archive. Phase 1 provides secure local audio import and a polished archive UI; local transcription, playback, summaries, and semantic search intentionally remain future phases.

## Architecture

- `frontend/`: React + TypeScript + Vite + Tailwind CSS presentation layer, with a typed API client.
- `backend/`: FastAPI + SQLAlchemy + SQLite API.
- Audio is written to `backend/data/audio/`, never to a frontend/public directory. SQLite is at `backend/data/lastwhisper.sqlite3` by default.
- The API uses generated UUID filenames, extension/MIME validation, streaming size limits, and resolved-path checks. It never executes uploaded files.
- Database strategy for Phase 1: schema creation on startup. Add Alembic revision scripts before making schema-changing production releases.

## Run locally

Requires Python 3.12+ and Node 20+.

```bash
# terminal 1 — API
cd backend
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp ../.env.example .env
uvicorn app.main:app --reload --port 8000
```

```bash
# terminal 2 — web application
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Check the service directly with `curl http://localhost:8000/api/health`.

## API

- `GET /api/health`
- `GET /api/recordings` and `GET /api/recordings/stats`
- `POST /api/recordings/import` (`file` multipart field; WAV, MP3, M4A, FLAC)
- `GET/PATCH/DELETE /api/recordings/{id}`

## Configuration

Copy `.env.example` to `backend/.env`. `STORAGE_DIR`, `DATABASE_URL`, `MAX_UPLOAD_SIZE_MB`, and `ALLOWED_ORIGINS` are configurable. Keep storage outside static/public frontend directories.

## Intentional Phase 1 limitations

Duration extraction, audio playback, local transcription, summaries, extracted memories, and semantic search are not implemented and the UI labels them plainly. No cloud AI, authentication, or remote storage is used.
