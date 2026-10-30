# Changelog

All notable changes to LastWhisper are documented here.

## 1.0.0 - 2026-10-30

### Added

- Local audio import, playback, archive management, and SQLite persistence.
- Local faster-whisper transcription with timestamped segments, progress, retry, and cancellation.
- Optional source-grounded Ollama summaries.
- Persistent local semantic search with sentence-transformers and SQLite float32 embeddings.
- Evidence-linked saved memories, editing, source navigation, deletion cleanup, and index rebuilding.
- Release hardening: readiness, trusted hosts, private file modes, audio signatures, staged deletion, safe re-transcription, lifespan-managed worker, Docker Compose, CI, and public documentation.

### Verified

- Real `base` Whisper CPU/int8 transcription and cached offline reload.
- Real 384-dimensional normalized embedding inference and cached offline reload.
- Two-recording opt-in integration journey including restart persistence and deletion cleanup.

### Known Limitations

- One API worker only.
- SQLite linear semantic ranking is intended for personal archives.
- No authentication or encryption at rest; localhost operation is required.
- Optional Ollama inference was not available in the release environment.
