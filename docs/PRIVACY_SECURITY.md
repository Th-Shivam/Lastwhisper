# Privacy and Security

LastWhisper handles voice recordings and transcripts as private personal data.

## Data Kept Locally by Default

- Original audio files.
- Recording metadata and filenames.
- Transcript text, language, timestamps, and errors.
- Optional summary and extracted entities.
- Search chunks, source IDs, model metadata, and embedding vectors.
- Saved-memory titles, notes, evidence snapshots, and timestamps.

These values are plaintext in local files and SQLite. Use full-disk encryption and a private operating-system account for stronger at-rest protection.

## Network Activity

| Operation | Network behavior |
| --- | --- |
| Normal cached Whisper inference | None |
| Normal cached embedding inference/search | None |
| First uncached model load | Downloads model files from the configured registry |
| Optional Ollama summary | Sends full transcript text to the configured Ollama URL |
| Telemetry/analytics | None included |

Model downloads do not upload recordings or transcript text. Set both local-files-only flags after caching to reject missing-weight network fallback.

## Controls

- UUID storage names and resolved-path containment.
- Audio extension, MIME hint, size, and format-signature allowlists.
- Multipart/body limits in the app and supplied Nginx configuration.
- Owner-only POSIX permissions where supported.
- SQLAlchemy parameter binding and hidden exception parameters.
- Foreign-key cascades for source-linked deletion.
- Staged audio deletion with startup retry.
- Non-cacheable audio responses and canonical MIME types.
- Local trusted-host and CORS defaults.
- Local-only Ollama default with bounded responses.
- No external font, analytics, or telemetry request in the frontend.

## Important Non-Goals

The app does not provide authentication, authorization, TLS termination, multi-user isolation, audit logging, encrypted fields, malware scanning, or internet-facing denial-of-service protection. Do not expose it publicly. For shared-machine use, protect the OS account and data directory.

## Erasure

Deleting a recording removes database rows for its segments, index, and saved memories, then deletes the staged audio file. Docker users must also remove named volumes if they intend to erase the entire archive and model cache.

## Backups

Stop the API and copy the complete native `backend/data/` directory or Docker data volume. Backups contain the same private plaintext and require equivalent protection.
