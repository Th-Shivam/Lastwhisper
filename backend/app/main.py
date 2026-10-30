import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .config import settings
from .database import SessionLocal, get_db, migrate_schema
from .models import Recording, SavedMemory, SearchChunk, SearchIndexState, TranscriptSegment
from .schemas import (
    IndexRebuildOut,
    IndexRebuildRequest,
    IndexStatusOut,
    MemoryCreate,
    MemoryDetailOut,
    MemoryUpdate,
    RecordingDetailOut,
    RecordingListOut,
    RecordingOut,
    RecordingUpdate,
    SearchRequest,
    SearchResponse,
    StatsOut,
    TranscriptionStart,
)
from .transcription import (
    audio_path,
    finalization_lock,
    is_transcription_active,
    mark_transcription_cancelled,
    request_transcription,
    shutdown_worker,
    start_worker,
    TranscriptionQueueFull,
)
from .search import (
    CHUNKER_VERSION,
    SearchBusy,
    SearchUnavailable,
    get_index_status,
    rebuild_index,
    search as semantic_search,
    segment_ids_for_chunk,
)

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac"}
ALLOWED_MIME_TYPES = {"audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp4", "audio/x-m4a", "audio/flac", "audio/x-flac"}
CANONICAL_MIME_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".flac": "audio/flac"}
logger = logging.getLogger(__name__)
UPLOAD_STAGE_PREFIX = ".upload--"
DELETE_STAGE_PREFIX = ".delete--"


class UploadBodyTooLarge(RuntimeError):
    pass


class UploadBodyLimitMiddleware:
    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") != "/api/recordings/import":
            await self.application(scope, receive, send)
            return
        total = 0
        max_request_bytes = settings.max_upload_size_mb * 1024 * 1024 + 1024 * 1024

        async def limited_receive():
            nonlocal total
            message = await receive()
            if message.get("type") == "http.request":
                total += len(message.get("body", b""))
                if total > max_request_bytes:
                    raise UploadBodyTooLarge
            return message

        try:
            await self.application(scope, limited_receive, send)
        except UploadBodyTooLarge:
            response = JSONResponse(
                status_code=413,
                content={"detail": f"Request is too large. Audio limit is {settings.max_upload_size_mb} MB."},
            )
            await response(scope, receive, send)


def _set_private_permissions(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        logger.warning("Could not tighten local permissions for %s", path.name)


def _prepare_private_storage() -> None:
    paths = [settings.storage_path]
    if settings.database_path:
        paths.append(settings.database_path.parent)
    if settings.whisper_cache_path:
        paths.append(settings.whisper_cache_path)
    if settings.embedding_cache_path:
        paths.append(settings.embedding_cache_path)
    for path in paths:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        _set_private_permissions(path, 0o700)


def _reconcile_file_operations(db: Session) -> None:
    for staged in settings.storage_path.glob(f"{UPLOAD_STAGE_PREFIX}*"):
        stored_name = staged.name.removeprefix(UPLOAD_STAGE_PREFIX)
        recording = db.scalar(select(Recording).where(Recording.relative_audio_path == stored_name))
        destination = settings.storage_path / stored_name
        try:
            if recording and not destination.exists():
                staged.replace(destination)
                _set_private_permissions(destination, 0o600)
            else:
                staged.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not reconcile a staged local audio upload")

    for tombstone in settings.storage_path.glob(f"{DELETE_STAGE_PREFIX}*"):
        details = tombstone.name.removeprefix(DELETE_STAGE_PREFIX).split("--", 1)
        if len(details) != 2:
            try:
                tombstone.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove an invalid staged deletion file")
            continue
        recording_id, stored_name = details
        recording = db.get(Recording, recording_id)
        destination = (settings.storage_path / stored_name).resolve()
        if settings.storage_path not in destination.parents:
            continue
        try:
            if recording and not destination.exists():
                tombstone.replace(destination)
                _set_private_permissions(destination, 0o600)
            else:
                tombstone.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not reconcile a staged local audio deletion")


def _initialize_app() -> None:
    _prepare_private_storage()
    migrate_schema()
    if settings.database_path and settings.database_path.exists():
        _set_private_permissions(settings.database_path, 0o600)
    with SessionLocal() as db:
        _reconcile_file_operations(db)
        interrupted = db.scalars(select(Recording).where(Recording.transcription_status.in_(("pending", "processing", "cancelling")))).all()
        for recording in interrupted:
            has_previous_transcript = bool(db.scalar(
                select(TranscriptSegment.id).where(TranscriptSegment.recording_id == recording.id).limit(1)
            ))
            recording.transcription_status = "complete" if has_previous_transcript else "failed"
            recording.transcription_progress = 100 if has_previous_transcript else 0
            recording.error_message = "Processing was interrupted by an application restart. The previous transcript was kept." if has_previous_transcript else "Transcription was interrupted by an application restart. You can retry it."
        stale_extractions = db.scalars(select(Recording).where(Recording.extraction_status == "pending")).all()
        for recording in stale_extractions:
            recording.extraction_status = "unavailable"
        stale_indexes = db.scalars(select(SearchIndexState).where(SearchIndexState.status == "indexing")).all()
        for state in stale_indexes:
            state.status = "failed"
            state.error_message = "Indexing was interrupted by an application restart. Rebuild the index."
        if interrupted or stale_extractions or stale_indexes:
            db.commit()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _initialize_app()
    start_worker()
    try:
        yield
    finally:
        shutdown_worker()


app = FastAPI(title="LastWhisper API", version="1.0.0", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(UploadBodyLimitMiddleware)


@app.middleware("http")
async def reject_oversized_uploads(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in settings.cors_origins:
        return JSONResponse(status_code=403, content={"detail": "This browser origin is not allowed to modify the local archive."})
    if request.method == "POST" and request.url.path == "/api/recordings/import":
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                request_bytes = int(content_length)
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header."})
            max_request_bytes = settings.max_upload_size_mb * 1024 * 1024 + 1024 * 1024
            if request_bytes > max_request_bytes:
                return JSONResponse(status_code=413, content={"detail": f"Request is too large. Audio limit is {settings.max_upload_size_mb} MB."})
    return await call_next(request)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "lastwhisper-api"}


@app.get("/api/ready")
def readiness(db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        db.execute(select(1)).scalar_one()
        versions = set(db.scalars(text("SELECT version FROM schema_migrations")))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="The local database is not ready.") from exc
    if not {2, 3}.issubset(versions):
        raise HTTPException(status_code=503, detail="The local database migration is incomplete.")
    if not settings.storage_path.is_dir() or not os.access(settings.storage_path, os.R_OK | os.W_OK | os.X_OK):
        raise HTTPException(status_code=503, detail="The private audio storage directory is not writable.")
    return {"status": "ready", "service": "lastwhisper-api"}


@app.get("/api/recordings", response_model=list[RecordingListOut])
def list_recordings(db: Session = Depends(get_db)):
    return list(db.scalars(select(Recording).order_by(Recording.created_at.desc())))


@app.get("/api/recordings/stats", response_model=StatsOut)
def recording_stats(db: Session = Depends(get_db)):
    total, transcribed, duration = db.execute(select(func.count(Recording.id), func.count(Recording.id).filter(Recording.transcription_status == "complete"), func.coalesce(func.sum(Recording.duration_seconds), 0))).one()
    return StatsOut(total_recordings=total, transcribed_recordings=transcribed, total_duration_seconds=duration, saved_memories=db.scalar(select(func.count(SavedMemory.id))) or 0)

@app.post("/api/search", response_model=SearchResponse)
def search_memories(payload: SearchRequest, db: Session = Depends(get_db)):
    try:
        matches = semantic_search(db, payload.query, payload.recording_id, payload.language, payload.saved_only, payload.limit)
    except SearchUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return SearchResponse(
        results=[{
            "chunk_id": match.chunk.id,
            "recording_id": match.recording.id,
            "recording_title": match.recording.title,
            "recording_date": match.recording.created_at,
            "segment_id": match.chunk.primary_segment_id,
            "segment_ids": list(match.segment_ids),
            "start_seconds": match.chunk.start_seconds,
            "end_seconds": match.chunk.end_seconds,
            "text": match.chunk.text,
            "similarity": round(match.similarity, 6),
        } for match in matches],
        minimum_similarity=settings.search_min_similarity,
        score_explanation="Cosine similarity is a ranking signal from -1 to 1, not a probability or guarantee of correctness.",
    )


@app.post("/api/search/rebuild", response_model=IndexRebuildOut)
def rebuild_search_index(payload: IndexRebuildRequest, db: Session = Depends(get_db)):
    del payload
    try:
        result = rebuild_index(db)
    except SearchBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SearchUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return IndexRebuildOut(
        indexed_chunks=result.indexed_chunks,
        indexed_recordings=result.indexed_recordings,
        empty_recordings=result.empty_recordings,
        failed_recordings=result.failed_recordings,
        errors=list(result.errors),
    )


@app.get("/api/search/status", response_model=IndexStatusOut)
def search_status(db: Session = Depends(get_db)):
    current = get_index_status(db)
    return IndexStatusOut(
        available=current.available,
        model_status=current.model_status,
        model_loaded=current.model_loaded,
        configured_model=settings.embedding_model,
        configured_device=settings.embedding_device,
        total_chunks=current.total_chunks,
        indexed_recordings=current.indexed_recordings,
        pending_recordings=current.pending_recordings,
        failed_recordings=current.failed_recordings,
        rebuilding=current.rebuilding,
        error=current.error,
    )


def memory_detail(memory: SavedMemory, recording: Recording) -> MemoryDetailOut:
    return MemoryDetailOut(
        id=memory.id,
        recording_id=memory.recording_id,
        segment_id=memory.segment_id,
        title=memory.title,
        note=memory.note,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        source_text=memory.source_text,
        source_start_seconds=memory.source_start_seconds,
        source_end_seconds=memory.source_end_seconds,
        recording_title=recording.title,
        recording_date=recording.created_at,
    )


@app.get("/api/memories", response_model=list[MemoryDetailOut])
def list_memories(db: Session = Depends(get_db)):
    rows = db.execute(
        select(SavedMemory, Recording)
        .join(Recording, Recording.id == SavedMemory.recording_id)
        .order_by(SavedMemory.updated_at.desc(), SavedMemory.created_at.desc())
    )
    return [memory_detail(memory, recording) for memory, recording in rows]


@app.post("/api/memories", response_model=MemoryDetailOut, status_code=status.HTTP_201_CREATED)
def save_memory(payload: MemoryCreate, db: Session = Depends(get_db)):
    if payload.search_chunk_id:
        chunk = db.get(SearchChunk, payload.search_chunk_id)
        if not chunk:
            raise HTTPException(status_code=404, detail="Search result not found. Run the search again after rebuilding the index.")
        segment_ids = segment_ids_for_chunk(chunk)
        if chunk.primary_segment_id not in segment_ids:
            raise HTTPException(status_code=409, detail="This search result has invalid source attribution. Rebuild the index.")
        state = db.get(SearchIndexState, chunk.recording_id)
        if not state or not (
            state.status == "ready"
            and state.transcript_hash == chunk.transcript_hash
            and state.embedding_model == settings.embedding_model == chunk.embedding_model
            and state.embedding_revision == settings.embedding_revision == chunk.embedding_revision
            and state.embedding_dimension == chunk.embedding_dimension
            and state.chunk_characters == settings.search_chunk_characters
            and state.chunk_overlap_segments == settings.search_chunk_overlap_segments
            and state.chunker_version == CHUNKER_VERSION
        ):
            raise HTTPException(status_code=409, detail="This search result is stale. Run the search again after rebuilding the index.")
        source_segments = list(db.scalars(
            select(TranscriptSegment).where(TranscriptSegment.id.in_(segment_ids))
        ))
        if len(source_segments) != len(segment_ids) or any(
            source.recording_id != chunk.recording_id for source in source_segments
        ):
            raise HTTPException(status_code=409, detail="This search result has invalid source attribution. Rebuild the index.")
        segment = next(
            (source for source in source_segments if source.id == chunk.primary_segment_id),
            None,
        )
        source_text = chunk.text
        source_start = chunk.start_seconds
        source_end = chunk.end_seconds
    else:
        segment = db.get(TranscriptSegment, payload.segment_id)
        source_text = segment.text if segment else ""
        source_start = segment.start_seconds if segment else 0.0
        source_end = segment.end_seconds if segment else 0.0
    if not segment:
        raise HTTPException(status_code=404, detail="Transcript segment not found.")
    recording = db.get(Recording, segment.recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Source recording not found.")
    with finalization_lock:
        db.refresh(recording)
        current_segment = db.get(TranscriptSegment, segment.id, populate_existing=True)
        if recording.transcription_status != "complete" or not current_segment or current_segment.recording_id != recording.id:
            raise HTTPException(status_code=409, detail="The source transcript is being replaced. Wait for transcription to finish and search again.")
        if db.scalar(select(SavedMemory.id).where(SavedMemory.segment_id == segment.id)):
            raise HTTPException(status_code=409, detail="This source segment is already saved.")
        memory = SavedMemory(
            id=str(uuid.uuid4()),
            recording_id=segment.recording_id,
            segment_id=segment.id,
            title=payload.title,
            note=payload.note,
            source_text=source_text,
            source_start_seconds=source_start,
            source_end_seconds=source_end,
        )
        db.add(memory)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="This source segment is already saved.") from exc
        db.refresh(memory)
    return memory_detail(memory, recording)


@app.patch("/api/memories/{memory_id}", response_model=MemoryDetailOut)
def update_memory(memory_id: str, payload: MemoryUpdate, db: Session = Depends(get_db)):
    memory = db.get(SavedMemory, memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Saved memory not found.")
    if "title" in payload.model_fields_set:
        memory.title = payload.title
    if "note" in payload.model_fields_set:
        memory.note = payload.note
    memory.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(memory)
    recording = db.get(Recording, memory.recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Source recording not found.")
    return memory_detail(memory, recording)


@app.delete("/api/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(memory_id: str, db: Session = Depends(get_db)):
    memory = db.get(SavedMemory, memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Saved memory not found.")
    db.delete(memory)
    db.commit()


def find_recording(recording_id: str, db: Session) -> Recording:
    recording = db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found.")
    return recording


def _inspect_audio_file(path: Path, extension: str) -> int | None:
    """Validate the real container and decode one audio frame with local PyAV."""
    try:
        import av
    except ImportError as exc:
        raise RuntimeError("Local audio validation is unavailable.") from exc

    expected_formats = {
        ".wav": {"wav"},
        ".mp3": {"mp3"},
        ".m4a": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
        ".flac": {"flac"},
    }
    try:
        with av.open(str(path), mode="r") as container:
            detected_formats = set(container.format.name.lower().split(","))
            if not detected_formats.intersection(expected_formats[extension]):
                raise ValueError("Audio container does not match its extension.")
            stream = next((candidate for candidate in container.streams if candidate.type == "audio"), None)
            if stream is None:
                raise ValueError("No audio stream was found.")
            decoded = False
            for packet_index, packet in enumerate(container.demux(stream)):
                if any(frame.samples > 0 for frame in packet.decode()):
                    decoded = True
                    break
                if packet_index >= 100:
                    break
            if not decoded:
                raise ValueError("No decodable audio frames were found.")
            duration = None
            if container.duration is not None:
                duration = int(round(float(container.duration / av.time_base)))
            elif stream.duration is not None and stream.time_base is not None:
                duration = int(round(float(stream.duration * stream.time_base)))
            return duration if duration and duration > 0 else None
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("The audio container could not be decoded.") from exc


@app.get("/api/recordings/{recording_id}", response_model=RecordingDetailOut)
def get_recording(recording_id: str, db: Session = Depends(get_db)):
    return find_recording(recording_id, db)


@app.get("/api/recordings/{recording_id}/audio")
def recording_audio(recording_id: str, db: Session = Depends(get_db)):
    recording = find_recording(recording_id, db)
    try:
        path = audio_path(recording)
    except Exception:
        raise HTTPException(status_code=404, detail="The local audio file is unavailable.")
    media_type = CANONICAL_MIME_TYPES.get(Path(recording.relative_audio_path).suffix.lower(), recording.mime_type)
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.post("/api/recordings/{recording_id}/transcribe", response_model=RecordingOut, status_code=status.HTTP_202_ACCEPTED)
def start_transcription(recording_id: str, payload: TranscriptionStart | None = None, db: Session = Depends(get_db)):
    recording = find_recording(recording_id, db)
    if recording.transcription_status in {"pending", "processing", "cancelling"}:
        raise HTTPException(status_code=409, detail="This recording is already being transcribed.")
    payload = payload or TranscriptionStart()
    existing_segments = db.scalar(
        select(func.count(TranscriptSegment.id)).where(TranscriptSegment.recording_id == recording_id)
    ) or 0
    saved_memories = db.scalar(
        select(func.count(SavedMemory.id)).where(SavedMemory.recording_id == recording_id)
    ) or 0
    if existing_segments and not payload.replace_existing:
        detail = "Re-transcription replaces the current transcript and search index"
        if saved_memories:
            detail += f" and deletes {saved_memories} saved memory item(s) linked to its source segments"
        raise HTTPException(status_code=409, detail=f"{detail}. Resubmit with replace_existing=true to confirm.")
    try:
        audio_path(recording)
    except Exception:
        raise HTTPException(status_code=422, detail="The local audio file is unavailable or empty.")
    selected_language = payload.language
    previous_status = recording.transcription_status

    def prepare_job() -> None:
        with finalization_lock:
            db.refresh(recording)
            recording.transcription_status, recording.transcription_progress = "pending", 0
            recording.error_message = None
            recording.selected_language = selected_language
            db.commit()
            db.refresh(recording)

    try:
        queued = request_transcription(recording.id, selected_language, prepare_job)
    except TranscriptionQueueFull as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        recording = find_recording(recording_id, db)
        recording.transcription_status = previous_status
        recording.transcription_progress = 100 if existing_segments else 0
        recording.error_message = "The local transcription worker could not be started. Try again."
        db.commit()
        raise HTTPException(status_code=503, detail=recording.error_message) from exc
    if not queued:
        raise HTTPException(status_code=409, detail="This recording is already being transcribed.")
    return recording


@app.post("/api/recordings/{recording_id}/transcribe/cancel", response_model=RecordingOut)
def cancel_transcription(recording_id: str, db: Session = Depends(get_db)):
    find_recording(recording_id, db)
    recording = mark_transcription_cancelled(recording_id, db)
    if recording is None:
        raise HTTPException(status_code=409, detail="There is no active transcription to cancel.")
    return recording


@app.post("/api/recordings/import", response_model=RecordingOut, status_code=status.HTTP_201_CREATED)
def import_recording(file: UploadFile = File(...), db: Session = Depends(get_db)):
    original_name = (file.filename or "audio").replace("\\", "/").rsplit("/", 1)[-1]
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Unsupported file type. Use WAV, MP3, M4A, or FLAC.")
    # Browsers and curl often label local audio as application/octet-stream; the
    # extension is still required and the content type is only an extra signal.
    if file.content_type and file.content_type not in ALLOWED_MIME_TYPES | {"application/octet-stream"}:
        raise HTTPException(status_code=415, detail="This file does not appear to be a supported audio format.")
    stored_name = f"{uuid.uuid4()}{extension}"
    destination = settings.storage_path / stored_name
    staged_upload = settings.storage_path / f"{UPLOAD_STAGE_PREFIX}{stored_name}"
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    size = 0
    try:
        with staged_upload.open("xb") as buffer:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=413, detail=f"File is too large. Limit is {settings.max_upload_size_mb} MB.")
                buffer.write(chunk)
    except Exception:
        staged_upload.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()
    if size == 0:
        staged_upload.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="The audio file is empty.")
    try:
        probed_duration = _inspect_audio_file(staged_upload, extension)
    except RuntimeError as exc:
        staged_upload.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="Local audio validation is unavailable. Reinstall the backend dependencies.") from exc
    except ValueError as exc:
        staged_upload.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"The file is not a decodable {extension.removeprefix('.').upper()} audio file, or its container does not match the extension.") from exc
    if probed_duration and probed_duration > settings.max_processing_duration_seconds:
        staged_upload.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="This recording exceeds the configured processing duration limit.")
    _set_private_permissions(staged_upload, 0o600)
    title = Path(original_name).stem[:255] or "Untitled recording"
    recording = Recording(id=str(uuid.uuid4()), title=title, original_filename=original_name[:255], relative_audio_path=stored_name, mime_type=CANONICAL_MIME_TYPES[extension], file_size=size, duration_seconds=probed_duration, transcription_status="not_started")
    db.add(recording)
    try:
        db.commit()
    except Exception:
        db.rollback()
        staged_upload.unlink(missing_ok=True)
        raise
    db.refresh(recording)
    try:
        staged_upload.replace(destination)
        _set_private_permissions(destination, 0o600)
    except OSError as exc:
        db.delete(recording)
        db.commit()
        staged_upload.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="The uploaded audio could not be finalized in private storage.") from exc
    return recording


@app.patch("/api/recordings/{recording_id}", response_model=RecordingOut)
def update_recording(recording_id: str, payload: RecordingUpdate, db: Session = Depends(get_db)):
    recording = find_recording(recording_id, db)
    recording.title = payload.title.strip()
    if not recording.title:
        raise HTTPException(status_code=422, detail="A title is required.")
    db.commit(); db.refresh(recording)
    return recording


@app.delete("/api/recordings/{recording_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recording(recording_id: str, db: Session = Depends(get_db)):
    recording = find_recording(recording_id, db)
    if is_transcription_active(recording_id):
        raise HTTPException(status_code=409, detail="Cancel the active transcription and wait for it to stop before deleting this recording.")
    source_path = (settings.storage_path / recording.relative_audio_path).resolve()
    if settings.storage_path not in source_path.parents:
        raise HTTPException(status_code=500, detail="Unsafe storage path.")
    tombstone = settings.storage_path / f"{DELETE_STAGE_PREFIX}{recording.id}--{recording.relative_audio_path}"
    try:
        if source_path.exists():
            source_path.replace(tombstone)
            _set_private_permissions(tombstone, 0o600)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="The local audio file could not be staged for deletion.") from exc
    try:
        db.delete(recording)
        db.commit()
    except Exception:
        db.rollback()
        if tombstone.exists():
            tombstone.replace(source_path)
        raise
    try:
        tombstone.unlink(missing_ok=True)
    except OSError:
        logger.warning("A staged audio deletion will be retried at the next application start")
