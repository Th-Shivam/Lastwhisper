# Release Verification

Date: 2026-10-30

## Environment

- AMD Ryzen 5 5600H, 6 cores / 12 threads
- 14 GiB RAM
- Linux x86_64
- Python 3.13.12
- Node.js 20.19.5
- CPU-only Torch 2.15.0
- CTranslate2 4.8.4

## Real Whisper

Verified with the configured `base` model, CPU device, and `int8` compute type against a disposable 9.51-second WAV generated locally with FFmpeg's Flite filter.

- Model loaded successfully.
- Detected language: English (`en`).
- Returned two non-empty segments with source timestamps.
- Cached model loaded again with `WHISPER_LOCAL_FILES_ONLY=true`, `HF_HUB_OFFLINE=1`, and outbound socket creation blocked.
- Initial verification exposed and fixed an incompatible unconstrained PyAV 19 resolution; release requirements pin PyAV 16.1.0.

## Real Embeddings

Verified the pinned `paraphrase-multilingual-MiniLM-L12-v2` revision on CPU.

- Dimension: 384.
- Two generated vector norms: `1.0000000365` and `1.0000000140`.
- Cached model loaded with `EMBEDDING_LOCAL_FILES_ONLY=true`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, and outbound socket creation blocked.
- Real semantic ranking returned the intended recording for project/privacy and garden/family queries with correct recording IDs, source segment IDs, and timestamps.

The registry client's optional Xet transfer stalled in this environment. Standard HTTPS retrieval of the exact pinned safetensors/tokenizer files succeeded, and Xet is disabled by default in release configuration for predictable downloads.

## End-to-End API Journey

The opt-in integration test used two real synthesized-speech WAV files and cached real models:

1. Started the app lifespan and passed readiness.
2. Imported both audio files and verified local retrieval.
3. Completed real transcription and language/timestamp checks.
4. Automatically indexed both transcripts with the real embedding model.
5. Retrieved the intended evidence for two natural-language topics.
6. Saved and edited source-linked memories.
7. Searched the original topic again.
8. Deleted one recording and verified chunk, state, and memory cleanup.
9. Restarted the app and verified the remaining recording, segments, and edited memory persisted.

Result: `1 passed in 7.99s` after model caches were warm on the final release code.

## Optional Ollama

Ollama was not installed in the release environment. Real summary/entity inference is **not verified**. The unconfigured path was verified to remain unavailable without inventing a summary, and a unit test verifies that remote Ollama URLs are rejected by default.

## Frontend and Docker

- Production TypeScript/Vite build passed.
- ESLint passed.
- Two DOM tests passed and cover application mounting, mobile navigation, semantic evidence navigation, and source-audio timestamp seeking.
- Real FFmpeg-generated WAV, MP3, M4A, and FLAC files passed the PyAV container/decode validator; corrupt and extension-mismatched uploads were rejected.
- npm audit reported zero vulnerabilities.
- CPU backend and Nginx frontend images built successfully from a clean Docker context.
- Full Docker Compose startup passed both health checks; the same-origin `/api/ready` proxy returned `ready`, and containers ran with persistent data/model volumes.
- Automated real-browser audio playback/seek was not available. API byte-range delivery, exact source timestamps, player wiring, and production build were verified.
