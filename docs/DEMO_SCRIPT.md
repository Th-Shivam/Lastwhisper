# Hacktoberfest Week 4 Demo Script

Target length: 2–3 minutes. Use a short recording you own and have permission to demonstrate. Do not use fake transcripts or screenshots.

## Before Recording

- Cache and verify both default models.
- Set both local-files-only flags to `true`.
- Use the native CPU setup unless Docker has already cached its own model volume.
- Prepare one 10–20 second voice note that clearly mentions a project topic.
- Start the API, frontend, and screen recorder; confirm `/api/ready` is healthy.
- Keep the Memory Search status visible long enough to show the local model/index state.

## 0:00–0:20 — The Promise

Open the empty or existing archive.

> “LastWhisper is a local-first voice memory archive. Personal recordings become searchable memories without sending audio or transcripts to hosted AI APIs.”

Point briefly to the Local-first label and About page. Qualify the offline claim:

> “The open models need to be downloaded once. This demo is using already cached weights with offline-only mode enabled.”

## 0:20–0:55 — Import and Transcribe

Import the prepared voice note and open it.

> “The file receives a generated private storage name and stays outside the web root. faster-whisper is now transcribing it locally on CPU.”

Show progress, detected language, and timestamped segments. Play a few seconds of the original recording.

## 0:55–1:25 — Evidence-Grounded Search

Open Memory Search and ask:

> “What did I discuss about my project?”

Show the result's recording title, date, exact source timestamp, transcript evidence, and cosine score.

> “This is retrieval, not a fabricated answer. The score ranks normalized local embeddings; the text is genuine transcript evidence.”

## 1:25–1:50 — Return to the Source

Select **Open source**. Show that the recording opens at the result timestamp, then play it.

> “Every result remains auditable against the voice recording that produced it.”

Return to Memory Search; the search state remains available.

## 1:50–2:10 — Save a Memory

Save the result, open Saved memories, rename it, and add a short personal note.

> “The title and note are editable, but the source transcript snapshot is immutable.”

## 2:10–2:40 — Why Open Matters

> “Whisper, CTranslate2, sentence-transformers, SQLite, FastAPI, and React are replaceable open technologies. The default embedding model is pinned to an immutable revision. There is no hosted AI lock-in, configured telemetry exporter, or cloud storage dependency.”

Connect to the Week 4 theme:

> “For ‘Ghost in the Machine,’ LastWhisper honors the memories and voices we carry from the past while keeping them private on the owner's machine.”

## 2:40–3:00 — Honest Limitations

> “This release supports one local API process, CPU-first inference, and personal-scale SQLite search. Models consume disk and RAM, cancellation is cooperative, and Ollama summaries are optional. The API is not authenticated, so it must stay on localhost.”

End on the source evidence or saved memory, not on a simulated output.

## Concise Feature Summary

- Local audio archive and secure deletion.
- Real local Whisper transcription with timestamps.
- Real local semantic embeddings and evidence retrieval.
- Exact source navigation and playback.
- Source-linked editable saved memories.
- Persistent SQLite archive and rebuildable index.
- Explicit offline/cache status with no hosted AI requirement.

## Architecture Talking Points

- React talks to a loopback FastAPI API.
- Audio stays in a private filesystem directory.
- SQLAlchemy persists source records and float32 embeddings in SQLite.
- One worker serializes native model inference.
- Model registries are needed only for uncached weights.
- Optional Ollama is localhost-only by default.

## Verified Open-Source Technologies

- React, Vite, Tailwind CSS, and Lucide.
- FastAPI, SQLAlchemy, and SQLite.
- faster-whisper, CTranslate2, and OpenAI Whisper model weights.
- sentence-transformers, PyTorch, and the Apache-2.0 multilingual MiniLM model.
- FFmpeg for the disposable release audio fixtures.

## Hardware and Limitations

- Verified on a Ryzen 5 5600H with 14 GiB RAM using CPU/int8 Whisper.
- Recommend at least 8 GiB RAM and roughly 3 GiB free for dependencies plus default caches.
- Larger Whisper or Ollama models need more RAM/VRAM.
- Optional Ollama output was not real-model verified in the release environment.
- Do not claim all machines are offline-ready until their own caches pass offline-only verification.

## Challenge Submission Notes

The live Week 4 challenge is **Hacktoberfest Open-Source AI Challenge: Week 4**, event ID 82. The challenge page states submissions are due October 31, 2026 at 11:59 PM PDT, equivalent to November 1 at 06:59 UTC. Required DEV tag: `#hf26challenge`. Judging weights writing quality most heavily, followed by theme relevance, creativity, and technical execution. The DEV post should explain why local open innovation matters, link the public code plus a real demo, credit all prior work, and use DEV's honest AI-disclosure setting (`some_ai`) for agent-assisted writing and implementation.
