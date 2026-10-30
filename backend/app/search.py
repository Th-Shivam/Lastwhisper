"""Rebuildable, local-only semantic search backed by SQLite.

Embeddings are normalized float32 vectors serialized as little-endian bytes. The
associated model, revision, dimension, transcript fingerprint, segment IDs, and
source timestamps are stored alongside each chunk so incompatible or malformed
rows can be ignored safely.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import struct
import threading
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import (
    Recording,
    SavedMemory,
    SearchChunk,
    SearchIndexState,
    TranscriptSegment,
)

CHUNKER_VERSION = "1"


class SearchUnavailable(RuntimeError):
    """Raised when local embedding generation cannot be used safely."""


class SearchBusy(RuntimeError):
    """Raised when another full index rebuild is already running."""


class SearchIndexChanged(RuntimeError):
    """Raised when a transcript changes while its embeddings are being built."""


@dataclass(frozen=True)
class ChunkDraft:
    start_seconds: float
    end_seconds: float
    text: str
    segment_ids: tuple[str, ...]
    primary_segment_id: str


@dataclass(frozen=True)
class IndexResult:
    recording_id: str
    indexed_chunks: int
    skipped: bool
    status: str


@dataclass(frozen=True)
class RebuildResult:
    indexed_chunks: int
    indexed_recordings: int
    empty_recordings: int
    failed_recordings: int
    errors: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class SearchMatch:
    similarity: float
    chunk: SearchChunk
    recording: Recording
    segment_ids: tuple[str, ...]


@dataclass(frozen=True)
class EmbeddingRuntimeStatus:
    available: bool
    status: str
    loaded: bool
    error: str | None


@dataclass(frozen=True)
class IndexStatus:
    available: bool
    model_status: str
    model_loaded: bool
    total_chunks: int
    indexed_recordings: int
    pending_recordings: int
    failed_recordings: int
    rebuilding: bool
    error: str | None


@dataclass(frozen=True)
class _ChunkUnit:
    segment_id: str
    start_seconds: float
    end_seconds: float
    text: str


_model = None
_model_key: tuple[str, str, str | None, str | None, bool] | None = None
_model_error: str | None = None
_model_lock = threading.Lock()
_rebuild_lock = threading.Lock()
_index_locks_guard = threading.Lock()
_index_locks: dict[str, threading.Lock] = {}
_rebuilding = False


def _embedding_key() -> tuple[str, str, str | None, str | None, bool]:
    return (
        settings.embedding_model,
        settings.embedding_device,
        settings.embedding_revision,
        str(settings.embedding_cache_path) if settings.embedding_cache_path else None,
        settings.embedding_local_files_only,
    )


def _model_error_message() -> str:
    cache_hint = (
        "Disable EMBEDDING_LOCAL_FILES_ONLY once while online to populate the cache."
        if settings.embedding_local_files_only
        else "Cache the model once while online before relying on offline search."
    )
    return (
        f"The local embedding model '{settings.embedding_model}' could not be used "
        f"on device '{settings.embedding_device}'. Install the backend dependencies "
        f"and verify the model/device settings. {cache_hint}"
    )


def get_embedder():
    """Lazily load and cache one sentence-transformer per configured model key."""
    global _model, _model_error, _model_key
    key = _embedding_key()
    with _model_lock:
        if _model is not None and _model_key == key:
            return _model
        _model = None
        _model_key = key
        _model_error = None
        try:
            from sentence_transformers import SentenceTransformer

            kwargs: dict[str, object] = {
                "device": settings.embedding_device,
                "local_files_only": settings.embedding_local_files_only,
            }
            if settings.embedding_revision:
                kwargs["revision"] = settings.embedding_revision
            if settings.embedding_cache_path:
                kwargs["cache_folder"] = str(settings.embedding_cache_path)
            _model = SentenceTransformer(settings.embedding_model, **kwargs)
            return _model
        except Exception as exc:
            _model_error = _model_error_message()
            raise SearchUnavailable(_model_error) from exc


def reset_embedding_cache() -> None:
    """Forget the in-process model cache; primarily useful for configuration tests."""
    global _model, _model_error, _model_key
    with _model_lock:
        _model = None
        _model_key = None
        _model_error = None


def embedding_runtime_status() -> EmbeddingRuntimeStatus:
    """Inspect dependency/runtime state without loading or downloading weights."""
    key = _embedding_key()
    with _model_lock:
        if _model_key == key and _model_error:
            return EmbeddingRuntimeStatus(False, "error", _model is not None, _model_error)
        if _model_key == key and _model is not None:
            return EmbeddingRuntimeStatus(True, "loaded", True, None)
    if importlib.util.find_spec("sentence_transformers") is None:
        return EmbeddingRuntimeStatus(
            False,
            "unavailable",
            False,
            "sentence-transformers is not installed. Install backend/requirements.txt before using semantic search.",
        )
    return EmbeddingRuntimeStatus(False, "not_loaded", False, None)


def normalize_vector(vector: Iterable[float]) -> list[float]:
    values = [float(value) for value in vector]
    if not values or not all(math.isfinite(value) for value in values):
        raise ValueError("Embedding vectors must be non-empty and finite.")
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("Embedding vectors must have a non-zero finite norm.")
    return [value / norm for value in values]


def pack(vector: Iterable[float]) -> bytes:
    """Serialize a finite float vector as little-endian float32 bytes."""
    values = [float(value) for value in vector]
    if not values or not all(math.isfinite(value) for value in values):
        raise ValueError("Embedding vectors must be non-empty and finite.")
    return struct.pack(f"<{len(values)}f", *values)


def unpack(data: bytes, expected_dimension: int | None = None) -> list[float]:
    """Deserialize and validate a little-endian float32 vector."""
    if not data or len(data) % 4:
        raise ValueError("Malformed float32 embedding payload.")
    dimension = len(data) // 4
    if expected_dimension is not None and dimension != expected_dimension:
        raise ValueError("Stored embedding dimension does not match its metadata.")
    values = list(struct.unpack(f"<{dimension}f", data))
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Stored embedding contains a non-finite value.")
    return values


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Return cosine similarity in [-1, 1]; the value is not a probability."""
    if not a or len(a) != len(b):
        raise ValueError("Cosine similarity requires equal, non-empty vectors.")
    if not all(math.isfinite(float(value)) for value in (*a, *b)):
        raise ValueError("Cosine similarity requires finite vectors.")
    dot = sum(float(x) * float(y) for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(float(x) * float(x) for x in a))
    norm_b = math.sqrt(sum(float(y) * float(y) for y in b))
    if not norm_a or not norm_b:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


def _encode(texts: Sequence[str]) -> list[list[float]]:
    global _model_error
    if not texts:
        return []
    model = get_embedder()
    try:
        encoded = list(model.encode(list(texts), normalize_embeddings=True))
        if len(encoded) != len(texts):
            raise ValueError("The embedding model returned an unexpected vector count.")
        vectors = []
        for vector in encoded:
            raw = vector.tolist() if hasattr(vector, "tolist") else list(vector)
            vectors.append(normalize_vector(raw))
        dimension = len(vectors[0])
        if any(len(vector) != dimension for vector in vectors):
            raise ValueError("The embedding model returned inconsistent dimensions.")
        with _model_lock:
            _model_error = None
        return vectors
    except SearchUnavailable:
        raise
    except Exception as exc:
        message = (
            "Local embedding generation failed. Verify the configured model and device, "
            "then rebuild the index. No transcript content was sent externally."
        )
        with _model_lock:
            _model_error = message
        raise SearchUnavailable(message) from exc


def _split_segment(segment: TranscriptSegment, max_characters: int) -> list[_ChunkUnit]:
    text = " ".join(segment.text.split())
    if not text:
        return []
    source_start = float(segment.start_seconds)
    source_end = float(segment.end_seconds)
    if (
        not math.isfinite(source_start)
        or not math.isfinite(source_end)
        or source_start < 0
        or source_end < source_start
    ):
        raise ValueError("Transcript segments must have finite, ordered source timestamps.")
    units: list[_ChunkUnit] = []
    cursor = 0
    while cursor < len(text):
        hard_end = min(len(text), cursor + max_characters)
        end = hard_end
        if hard_end < len(text):
            boundary = text.rfind(" ", cursor + 1, hard_end + 1)
            if boundary > cursor:
                end = boundary
        piece = text[cursor:end].strip()
        if piece:
            # Whisper only provides segment-level timing. Reuse those exact source
            # bounds instead of inventing sub-segment timestamps from text length.
            units.append(_ChunkUnit(segment.id, source_start, source_end, piece))
        cursor = max(end, cursor + 1)
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
    return units


def _joined_size(units: Sequence[_ChunkUnit]) -> int:
    return sum(len(unit.text) for unit in units) + max(0, len(units) - 1)


def _overlap_tail(units: Sequence[_ChunkUnit], segment_count: int) -> list[_ChunkUnit]:
    if segment_count <= 0:
        return []
    selected: list[_ChunkUnit] = []
    seen: set[str] = set()
    for unit in reversed(units):
        if unit.segment_id not in seen and len(seen) >= segment_count:
            break
        selected.append(unit)
        seen.add(unit.segment_id)
    selected.reverse()
    return selected


def _draft(units: Sequence[_ChunkUnit]) -> ChunkDraft:
    segment_ids = tuple(dict.fromkeys(unit.segment_id for unit in units))
    return ChunkDraft(
        start_seconds=units[0].start_seconds,
        end_seconds=units[-1].end_seconds,
        text=" ".join(unit.text for unit in units),
        segment_ids=segment_ids,
        primary_segment_id=segment_ids[0],
    )


def chunks_for_segments(
    segments: Sequence[TranscriptSegment],
    max_characters: int | None = None,
    overlap_segments: int | None = None,
) -> list[ChunkDraft]:
    """Split ordered segments into bounded chunks with source-segment overlap."""
    limit = max_characters if max_characters is not None else settings.search_chunk_characters
    overlap = overlap_segments if overlap_segments is not None else settings.search_chunk_overlap_segments
    if limit <= 0 or overlap < 0:
        raise ValueError("Chunk size must be positive and overlap cannot be negative.")
    units = [
        unit
        for segment in sorted(segments, key=lambda item: item.position)
        for unit in _split_segment(segment, limit)
    ]
    output: list[ChunkDraft] = []
    current: list[_ChunkUnit] = []
    for unit in units:
        if current and _joined_size([*current, unit]) > limit:
            output.append(_draft(current))
            current = _overlap_tail(current, overlap)
            while current and _joined_size([*current, unit]) > limit:
                current.pop(0)
        current.append(unit)
    if current:
        output.append(_draft(current))
    return output


def transcript_fingerprint(segments: Sequence[TranscriptSegment]) -> str:
    payload = [
        {
            "id": segment.id,
            "position": segment.position,
            "start": float(segment.start_seconds),
            "end": float(segment.end_seconds),
            "text": segment.text,
        }
        for segment in sorted(segments, key=lambda item: item.position)
    ]
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _state_matches(state: SearchIndexState, transcript_hash: str) -> bool:
    return (
        state.status in {"ready", "empty"}
        and state.transcript_hash == transcript_hash
        and state.embedding_model == settings.embedding_model
        and state.embedding_revision == settings.embedding_revision
        and state.chunk_characters == settings.search_chunk_characters
        and state.chunk_overlap_segments == settings.search_chunk_overlap_segments
        and state.chunker_version == CHUNKER_VERSION
    )


def _write_state(
    db: Session,
    recording_id: str,
    transcript_hash: str,
    status: str,
    *,
    dimension: int | None = None,
    chunk_count: int = 0,
    error: str | None = None,
) -> SearchIndexState:
    state = db.get(SearchIndexState, recording_id)
    if state is None:
        state = SearchIndexState(
            recording_id=recording_id,
            transcript_hash=transcript_hash,
            embedding_model=settings.embedding_model,
            embedding_revision=settings.embedding_revision,
            embedding_dimension=dimension,
            chunk_characters=settings.search_chunk_characters,
            chunk_overlap_segments=settings.search_chunk_overlap_segments,
            chunker_version=CHUNKER_VERSION,
            chunk_count=chunk_count,
            status=status,
            error_message=error,
            indexed_at=None,
        )
        db.add(state)
    else:
        state.transcript_hash = transcript_hash
        state.embedding_model = settings.embedding_model
        state.embedding_revision = settings.embedding_revision
        state.embedding_dimension = dimension
        state.chunk_characters = settings.search_chunk_characters
        state.chunk_overlap_segments = settings.search_chunk_overlap_segments
        state.chunker_version = CHUNKER_VERSION
        state.chunk_count = chunk_count
        state.status = status
        state.error_message = error
    state.updated_at = datetime.now(timezone.utc)
    state.indexed_at = datetime.now(timezone.utc) if status in {"ready", "empty"} else None
    return state


def invalidate_recording_index(recording_id: str, db: Session, *, commit: bool = True) -> None:
    """Remove derived search data before a transcript is replaced or deleted."""
    db.execute(delete(SearchChunk).where(SearchChunk.recording_id == recording_id))
    db.execute(delete(SearchIndexState).where(SearchIndexState.recording_id == recording_id))
    if commit:
        db.commit()


def _index_lock(recording_id: str) -> threading.Lock:
    with _index_locks_guard:
        return _index_locks.setdefault(recording_id, threading.Lock())


def index_recording(recording_id: str, db: Session, *, force: bool = False) -> IndexResult:
    with _index_lock(recording_id):
        return _index_recording(recording_id, db, force=force)


def _index_recording(recording_id: str, db: Session, *, force: bool = False) -> IndexResult:
    """Build or refresh one completed recording's index atomically by revision."""
    recording = db.get(Recording, recording_id)
    if not recording or recording.transcription_status != "complete":
        return IndexResult(recording_id, 0, False, "unavailable")

    segments = list(db.scalars(
        select(TranscriptSegment)
        .where(TranscriptSegment.recording_id == recording_id)
        .order_by(TranscriptSegment.position)
    ))
    transcript_hash = transcript_fingerprint(segments)
    state = db.get(SearchIndexState, recording_id)
    existing_count = db.scalar(
        select(func.count(SearchChunk.id)).where(SearchChunk.recording_id == recording_id)
    ) or 0
    usable_existing = bool(
        state is not None
        and _state_matches(state, transcript_hash)
        and existing_count == state.chunk_count
    )
    if state is not None and not force and _state_matches(state, transcript_hash):
        if existing_count == state.chunk_count:
            return IndexResult(recording_id, state.chunk_count, True, state.status)

    try:
        drafts = chunks_for_segments(segments)
    except (TypeError, ValueError) as exc:
        message = "The transcript contains invalid text or source timestamps and could not be indexed."
        if not usable_existing:
            _write_state(db, recording_id, transcript_hash, "failed", error=message)
            db.commit()
        raise SearchUnavailable(message) from exc
    if not drafts:
        db.execute(delete(SearchChunk).where(SearchChunk.recording_id == recording_id))
        _write_state(db, recording_id, transcript_hash, "empty")
        db.commit()
        return IndexResult(recording_id, 0, False, "empty")

    try:
        vectors = _encode([draft.text for draft in drafts])
        dimension = len(vectors[0])
    except SearchUnavailable as exc:
        if not usable_existing:
            _write_state(db, recording_id, transcript_hash, "failed", error=str(exc))
            db.commit()
        raise

    db.expire_all()
    current_recording = db.get(Recording, recording_id)
    current_segments = list(db.scalars(
        select(TranscriptSegment)
        .where(TranscriptSegment.recording_id == recording_id)
        .order_by(TranscriptSegment.position)
        .execution_options(populate_existing=True)
    ))
    if current_recording is None or current_recording.transcription_status != "complete":
        message = "The transcript changed while its search index was being built. Rebuild the index again."
        raise SearchIndexChanged(message)
    if transcript_fingerprint(current_segments) != transcript_hash:
        message = "The transcript changed while its search index was being built. Rebuild the index again."
        if not usable_existing:
            _write_state(db, recording_id, transcript_hash, "failed", error=message)
            db.commit()
        raise SearchIndexChanged(message)

    try:
        # The first write obtains SQLite's transaction lock. Re-check the source
        # after that point so a concurrent re-transcription either wins first or
        # invalidates these rows immediately after this commit.
        db.execute(delete(SearchChunk).where(SearchChunk.recording_id == recording_id))
        db.expire_all()
        locked_recording = db.get(Recording, recording_id)
        locked_segments = list(db.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.recording_id == recording_id)
            .order_by(TranscriptSegment.position)
            .execution_options(populate_existing=True)
        ))
        if (
            locked_recording is None
            or locked_recording.transcription_status != "complete"
            or transcript_fingerprint(locked_segments) != transcript_hash
        ):
            db.rollback()
            raise SearchIndexChanged(
                "The transcript changed while its search index was being built. Rebuild the index again."
            )
        for position, (draft, vector) in enumerate(zip(drafts, vectors, strict=True)):
            db.add(SearchChunk(
                id=str(uuid.uuid4()),
                recording_id=recording_id,
                position=position,
                primary_segment_id=draft.primary_segment_id,
                segment_ids=json.dumps(draft.segment_ids),
                start_seconds=draft.start_seconds,
                end_seconds=draft.end_seconds,
                text=draft.text,
                embedding=pack(vector),
                embedding_model=settings.embedding_model,
                embedding_revision=settings.embedding_revision,
                embedding_dimension=dimension,
                transcript_hash=transcript_hash,
            ))
        _write_state(
            db,
            recording_id,
            transcript_hash,
            "ready",
            dimension=dimension,
            chunk_count=len(drafts),
        )
        db.commit()
    except SearchIndexChanged:
        raise
    except Exception as exc:
        db.rollback()
        message = "The local search index could not be saved. Check the SQLite database and available disk space."
        if not usable_existing:
            _write_state(db, recording_id, transcript_hash, "failed", error=message)
            db.commit()
        raise SearchUnavailable(message) from exc
    return IndexResult(recording_id, len(drafts), False, "ready")


def remove_recording_chunks(recording_id: str, db: Session) -> int:
    result = db.execute(delete(SearchChunk).where(SearchChunk.recording_id == recording_id))
    db.execute(delete(SearchIndexState).where(SearchIndexState.recording_id == recording_id))
    db.commit()
    return int(result.rowcount or 0)


def rebuild_index(db: Session) -> RebuildResult:
    """Reconstruct every completed transcript index, continuing past row errors."""
    global _rebuilding
    if not _rebuild_lock.acquire(blocking=False):
        raise SearchBusy("A local search-index rebuild is already running.")
    _rebuilding = True
    try:
        recording_ids = list(db.scalars(
            select(Recording.id)
            .where(Recording.transcription_status == "complete")
            .order_by(Recording.created_at)
        ))
        if recording_ids:
            has_segments = db.scalar(
                select(func.count(TranscriptSegment.id)).where(
                    TranscriptSegment.recording_id.in_(recording_ids),
                    func.length(func.trim(TranscriptSegment.text)) > 0,
                )
            ) or 0
            if has_segments:
                get_embedder()

        indexed_chunks = 0
        indexed_recordings = 0
        empty_recordings = 0
        errors: list[dict[str, str]] = []
        for recording_id in recording_ids:
            try:
                result = index_recording(recording_id, db, force=True)
                indexed_chunks += result.indexed_chunks
                if result.status == "ready":
                    indexed_recordings += 1
                elif result.status == "empty":
                    empty_recordings += 1
            except (SearchUnavailable, SearchIndexChanged) as exc:
                errors.append({"recording_id": recording_id, "error": str(exc)})
        return RebuildResult(
            indexed_chunks=indexed_chunks,
            indexed_recordings=indexed_recordings,
            empty_recordings=empty_recordings,
            failed_recordings=len(errors),
            errors=tuple(errors),
        )
    finally:
        _rebuilding = False
        _rebuild_lock.release()


def segment_ids_for_chunk(chunk: SearchChunk) -> tuple[str, ...]:
    try:
        values = json.loads(chunk.segment_ids)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        return ()
    return tuple(dict.fromkeys(values))


def search(
    db: Session,
    query: str,
    recording_id: str | None = None,
    language: str | None = None,
    saved_only: bool = False,
    limit: int = 8,
) -> list[SearchMatch]:
    """Rank genuine transcript chunks by cosine similarity to a local query."""
    query_vector = _encode([query])[0]
    statement = (
        select(SearchChunk, Recording)
        .join(Recording, Recording.id == SearchChunk.recording_id)
        .join(SearchIndexState, SearchIndexState.recording_id == SearchChunk.recording_id)
        .where(
            Recording.transcription_status == "complete",
            SearchIndexState.status == "ready",
            SearchIndexState.embedding_model == settings.embedding_model,
            SearchIndexState.chunk_characters == settings.search_chunk_characters,
            SearchIndexState.chunk_overlap_segments == settings.search_chunk_overlap_segments,
            SearchIndexState.chunker_version == CHUNKER_VERSION,
            SearchChunk.transcript_hash == SearchIndexState.transcript_hash,
            SearchChunk.embedding_model == settings.embedding_model,
            SearchChunk.embedding_dimension == SearchIndexState.embedding_dimension,
        )
    )
    if settings.embedding_revision is None:
        statement = statement.where(
            SearchIndexState.embedding_revision.is_(None),
            SearchChunk.embedding_revision.is_(None),
        )
    else:
        statement = statement.where(
            SearchIndexState.embedding_revision == settings.embedding_revision,
            SearchChunk.embedding_revision == settings.embedding_revision,
        )
    if recording_id:
        statement = statement.where(SearchChunk.recording_id == recording_id)
    if language:
        statement = statement.where(func.lower(Recording.transcription_language) == language.lower())
    statement = statement.order_by(SearchChunk.recording_id, SearchChunk.position)

    saved_segment_ids = set(db.scalars(select(SavedMemory.segment_id))) if saved_only else set()

    ranked: list[SearchMatch] = []
    current_recording_id: str | None = None
    valid_segment_ids: set[str] = set()
    for chunk, recording in db.execute(statement).yield_per(256):
        if chunk.recording_id != current_recording_id:
            current_recording_id = chunk.recording_id
            valid_segment_ids = set(db.scalars(
                select(TranscriptSegment.id).where(
                    TranscriptSegment.recording_id == current_recording_id
                )
            ))
        segment_ids = segment_ids_for_chunk(chunk)
        if not segment_ids or any(segment_id not in valid_segment_ids for segment_id in segment_ids):
            continue
        if chunk.primary_segment_id not in valid_segment_ids:
            continue
        if saved_only and not saved_segment_ids.intersection(segment_ids):
            continue
        if chunk.embedding_dimension != len(query_vector):
            continue
        try:
            stored_vector = unpack(chunk.embedding, chunk.embedding_dimension)
            score = cosine(query_vector, stored_vector)
        except (TypeError, ValueError, struct.error):
            continue
        if score >= settings.search_min_similarity:
            ranked.append(SearchMatch(score, chunk, recording, segment_ids))
            if len(ranked) > limit:
                ranked.sort(key=lambda match: (-match.similarity, -match.recording.created_at.timestamp(), match.chunk.position))
                del ranked[limit:]
    ranked.sort(key=lambda match: (-match.similarity, -match.recording.created_at.timestamp(), match.chunk.position))
    return ranked[:limit]


def get_index_status(db: Session) -> IndexStatus:
    runtime = embedding_runtime_status()
    revision_clause = (
        SearchIndexState.embedding_revision.is_(None)
        if settings.embedding_revision is None
        else SearchIndexState.embedding_revision == settings.embedding_revision
    )
    current_config = (
        SearchIndexState.embedding_model == settings.embedding_model,
        revision_clause,
        SearchIndexState.chunk_characters == settings.search_chunk_characters,
        SearchIndexState.chunk_overlap_segments == settings.search_chunk_overlap_segments,
        SearchIndexState.chunker_version == CHUNKER_VERSION,
    )
    chunk_revision_clause = (
        SearchChunk.embedding_revision.is_(None)
        if settings.embedding_revision is None
        else SearchChunk.embedding_revision == settings.embedding_revision
    )
    indexed_recordings = db.scalar(
        select(func.count(SearchIndexState.recording_id))
        .join(Recording, Recording.id == SearchIndexState.recording_id)
        .where(
            *current_config,
            Recording.transcription_status == "complete",
            SearchIndexState.status.in_(("ready", "empty")),
        )
    ) or 0
    failed_recordings = db.scalar(
        select(func.count(SearchIndexState.recording_id))
        .join(Recording, Recording.id == SearchIndexState.recording_id)
        .where(
            *current_config,
            Recording.transcription_status == "complete",
            SearchIndexState.status == "failed",
        )
    ) or 0
    completed_recordings = db.scalar(
        select(func.count(Recording.id)).where(Recording.transcription_status == "complete")
    ) or 0
    total_chunks = db.scalar(
        select(func.count(SearchChunk.id))
        .join(SearchIndexState, SearchIndexState.recording_id == SearchChunk.recording_id)
        .join(Recording, Recording.id == SearchChunk.recording_id)
        .where(
            *current_config,
            Recording.transcription_status == "complete",
            SearchIndexState.status == "ready",
            SearchChunk.transcript_hash == SearchIndexState.transcript_hash,
            SearchChunk.embedding_model == settings.embedding_model,
            chunk_revision_clause,
            SearchChunk.embedding_dimension == SearchIndexState.embedding_dimension,
        )
    ) or 0
    return IndexStatus(
        available=runtime.available,
        model_status=runtime.status,
        model_loaded=runtime.loaded,
        total_chunks=total_chunks,
        indexed_recordings=indexed_recordings,
        pending_recordings=max(0, completed_recordings - indexed_recordings - failed_recordings),
        failed_recordings=failed_recordings,
        rebuilding=_rebuilding,
        error=runtime.error,
    )
