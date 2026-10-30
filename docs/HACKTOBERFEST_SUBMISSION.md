---
title: "LastWhisper: Keeping Voices Alive with Local Open-Source AI"
published: false
tags: devchallenge, hf26challenge, opensource, ai
---

<!-- Set DEV's AI disclosure to `some_ai` before publishing. Replace the demo placeholder only with a real public video. -->

*This is a submission for the [Hacktoberfest Open-Source AI Challenge Week 4: Ghost in the Machine](https://dev.to/challenges/hacktoberfest-week4-2026-10-26).*

## What I Built

LastWhisper is a local-first voice memory archive. It records the evidence behind a memory instead of generating a story about it: audio is transcribed locally, split into timestamped source segments, embedded locally for semantic search, and linked back to the exact recording and time where it came from.

I interpreted “Ghost in the Machine” as the voices and stories that remain with us. A recording can outlive the moment or person who made it, but preserving it should not require uploading private family history to a closed service. LastWhisper makes those voices searchable while keeping the archive under its owner's control.

The release supports:

- Private local WAV, MP3, M4A, and FLAC storage.
- CPU-first faster-whisper transcription with timestamped segments.
- Real semantic retrieval with sentence-transformers and SQLite.
- Source audio playback and exact timestamp navigation.
- Saved evidence with editable personal titles and notes.
- Optional local Ollama summaries.
- Model-cache status, offline-only modes, deletion cascades, and index rebuilding.

## Demo

**Before publishing:** add the real 2–3 minute demo video URL recorded from the verified release flow.

The prepared demo script is in [`docs/DEMO_SCRIPT.md`](https://github.com/Th-Shivam/Lastwhisper/blob/master/docs/DEMO_SCRIPT.md). It imports a real voice note, shows local transcription, asks “What did I discuss about my project?”, opens the evidence at its source timestamp, and saves it as a memory.

## Code

{% github Th-Shivam/Lastwhisper %}

The repository includes native and Docker CPU setup, persistent storage, architecture and privacy diagrams, model/license notices, CI, offline unit tests, an opt-in real-model integration test, and public contribution/security guidance.

## How I Built It

The frontend is React, TypeScript, and Vite. FastAPI exposes a loopback API backed by SQLAlchemy and SQLite. Audio lives outside the web root under UUID names.

One local worker loads faster-whisper through CTranslate2. A successful transcript produces ordered source segments with language and timestamps. Sentence-transformers then creates normalized 384-dimensional embeddings. LastWhisper stores each vector directly in SQLite as a little-endian float32 BLOB with its immutable model revision, dimension, transcript fingerprint, source segment IDs, faithful text, and time range.

At search time, the query is embedded locally and ranked against current compatible chunks with cosine similarity. The API returns evidence, not a generated answer. Saved memories preserve an immutable evidence snapshot while allowing a personal title and note.

The default open AI stack is:

- faster-whisper and CTranslate2 for local speech inference.
- OpenAI Whisper `base` model weights for the verified CPU path.
- sentence-transformers with `paraphrase-multilingual-MiniLM-L12-v2`, pinned to an immutable Apache-2.0 revision.
- PyTorch CPU for embeddings.
- Optional Ollama for local-only summary enrichment.

## Why Does Open Innovation Matter?

Voice memories are unusually sensitive. They reveal identity, health, relationships, locations, and history. A closed transcription or embedding API makes privacy depend on another company's retention policy, pricing, availability, and product decisions.

Open models made a different architecture possible:

- The archive can work after the model files are cached and the network is removed.
- Recordings and transcript text do not need to be uploaded to hosted AI providers.
- Users can replace the Whisper or embedding model instead of migrating away from an API contract.
- Evidence remains in ordinary files and SQLite rather than a proprietary vector service.
- The project can clearly state which model revision produced an embedding and rebuild it later.

Open does not automatically mean offline. LastWhisper says when weights are missing, documents the one-time downloads, and provides local-files-only settings that fail rather than silently reaching the network.

## The Spirit of It

The most important interaction is not the search box. It is clicking **Open source** and hearing the voice at the exact moment represented by the result. The machine helps retrieve the memory, but the original voice remains the authority.

## Verification and Honest Limits

The release was tested on a Ryzen 5 5600H with 14 GiB RAM:

- Real Whisper `base` CPU/int8 transcription returned detected English and timestamped segments.
- Real 384-dimensional sentence embeddings were normalized and semantically ranked across two recordings.
- Both caches reloaded with Hub offline flags and blocked outbound socket creation.
- An opt-in end-to-end test passed upload, real transcription, indexing, search, source attribution, memory editing, deletion cleanup, and restart persistence.
- 38 offline backend tests, two frontend DOM tests, TypeScript, ESLint, production build, Docker Compose health checks, npm audit, pip-audit, and `git diff --check` passed during release preparation.

Ollama was not installed in the release environment, so I am not claiming real summary generation was verified. Search is a personal-scale linear SQLite scan, inference runs in one API process, cancellation is cooperative, and the unauthenticated API must remain on localhost.

Full results and commands are in [`docs/VERIFICATION.md`](https://github.com/Th-Shivam/Lastwhisper/blob/master/docs/VERIFICATION.md).
