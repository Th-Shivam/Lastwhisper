# Contributing to LastWhisper

Thank you for helping improve a private, local-first memory tool.

## Before You Start

- Search existing issues before opening a duplicate.
- Discuss large architecture, schema, model, or privacy changes in an issue first.
- Never commit recordings, transcripts, SQLite databases, model weights, API keys, or personal data.
- Keep normal tests deterministic and offline. Put real-model checks behind the `integration` marker.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
cp .env.example backend/.env

cd frontend
npm ci
cp .env.example .env
```

Run the API from the repository root with one worker:

```bash
PYTHONPATH=backend uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Run the web app with `npm run dev` from `frontend/`.

## Required Checks

```bash
PYTHONPATH=backend python -m pytest -q backend/tests -m "not integration"
cd frontend
npm test
npm run typecheck
npm run lint
npm run build
npm audit --audit-level=moderate
cd ..
git diff --check
docker compose config
```

## Pull Requests

- Keep changes focused and explain user-visible behavior.
- Add tests for bug fixes and new API contracts.
- Describe privacy/network effects, migrations, and model-cache changes explicitly.
- Preserve existing databases; never solve migrations by telling users to delete data.
- Update README and `.env.example` when configuration changes.
- Credit external code, models, datasets, and assets with their licenses.

## Style

- Prefer minimal, readable functions and parameterized SQLAlchemy queries.
- Keep model imports lazy so the API can report actionable missing-cache errors.
- Follow the existing LastWhisper visual language rather than replacing it with a new design system.
- Accessible names, keyboard behavior, visible focus, mobile layouts, and reduced motion are release requirements.

## Reporting Security Issues

Do not open a public issue for a vulnerability involving private data exposure. Follow [`SECURITY.md`](SECURITY.md).
