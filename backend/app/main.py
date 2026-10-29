import uuid
from pathlib import Path
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .config import settings
from .database import get_db, migrate_schema
from .models import Recording
from .schemas import RecordingDetailOut, RecordingOut, RecordingUpdate, StatsOut, TranscriptionStart
from .transcription import audio_path, request_transcription

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac"}
ALLOWED_MIME_TYPES = {"audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp4", "audio/x-m4a", "audio/flac", "audio/x-flac"}

app = FastAPI(title="LastWhisper API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def startup() -> None:
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    migrate_schema()
    # Jobs do not survive a Python process restart; make interrupted work retryable.
    with next(get_db()) as db:
        interrupted = db.scalars(select(Recording).where(Recording.transcription_status.in_(("pending", "processing")))).all()
        for recording in interrupted:
            recording.transcription_status, recording.transcription_progress = "failed", 0
            recording.error_message = "Transcription was interrupted by an application restart. You can retry it."
        if interrupted:
            db.commit()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "lastwhisper-api"}


@app.get("/api/recordings", response_model=list[RecordingOut])
def list_recordings(db: Session = Depends(get_db)):
    return list(db.scalars(select(Recording).order_by(Recording.created_at.desc())))


@app.get("/api/recordings/stats", response_model=StatsOut)
def recording_stats(db: Session = Depends(get_db)):
    total, transcribed, duration = db.execute(select(func.count(Recording.id), func.count(Recording.id).filter(Recording.transcription_status == "complete"), func.coalesce(func.sum(Recording.duration_seconds), 0))).one()
    return StatsOut(total_recordings=total, transcribed_recordings=transcribed, total_duration_seconds=duration, saved_memories=0)


def find_recording(recording_id: str, db: Session) -> Recording:
    recording = db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found.")
    return recording


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
    return FileResponse(path, media_type=recording.mime_type, filename=recording.original_filename)


@app.post("/api/recordings/{recording_id}/transcribe", response_model=RecordingOut, status_code=status.HTTP_202_ACCEPTED)
def start_transcription(recording_id: str, payload: TranscriptionStart | None = None, db: Session = Depends(get_db)):
    recording = find_recording(recording_id, db)
    if recording.transcription_status in {"pending", "processing"}:
        raise HTTPException(status_code=409, detail="This recording is already being transcribed.")
    try:
        audio_path(recording)
    except Exception:
        raise HTTPException(status_code=422, detail="The local audio file is unavailable or empty.")
    recording.transcription_status, recording.transcription_progress = "pending", 0
    recording.error_message = None
    recording.selected_language = payload.language.lower() if payload and payload.language else None
    db.commit(); db.refresh(recording)
    if not request_transcription(recording.id, recording.selected_language):
        raise HTTPException(status_code=409, detail="This recording is already being transcribed.")
    return recording


@app.post("/api/recordings/{recording_id}/transcribe/cancel", response_model=RecordingOut)
def cancel_transcription(recording_id: str, db: Session = Depends(get_db)):
    recording = find_recording(recording_id, db)
    if recording.transcription_status not in {"pending", "processing"}:
        raise HTTPException(status_code=409, detail="There is no active transcription to cancel.")
    recording.transcription_status, recording.transcription_progress = "cancelled", 0
    recording.error_message = "Transcription cancelled."
    db.commit(); db.refresh(recording)
    return recording


@app.post("/api/recordings/import", response_model=RecordingOut, status_code=status.HTTP_201_CREATED)
def import_recording(file: UploadFile = File(...), db: Session = Depends(get_db)):
    original_name = Path(file.filename or "audio").name
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Unsupported file type. Use WAV, MP3, M4A, or FLAC.")
    # Browsers and curl often label local audio as application/octet-stream; the
    # extension is still required and the content type is only an extra signal.
    if file.content_type and file.content_type not in ALLOWED_MIME_TYPES | {"application/octet-stream"}:
        raise HTTPException(status_code=415, detail="This file does not appear to be a supported audio format.")
    stored_name = f"{uuid.uuid4()}{extension}"
    destination = settings.storage_path / stored_name
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    size = 0
    try:
        with destination.open("xb") as buffer:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=413, detail=f"File is too large. Limit is {settings.max_upload_size_mb} MB.")
                buffer.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()
    if size == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="The audio file is empty.")
    title = Path(original_name).stem[:255] or "Untitled recording"
    recording = Recording(id=str(uuid.uuid4()), title=title, original_filename=original_name[:255], relative_audio_path=stored_name, mime_type=file.content_type or "application/octet-stream", file_size=size, transcription_status="not_started")
    db.add(recording)
    db.commit()
    db.refresh(recording)
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
    audio_path = (settings.storage_path / recording.relative_audio_path).resolve()
    if settings.storage_path not in audio_path.parents:
        raise HTTPException(status_code=500, detail="Unsafe storage path.")
    db.delete(recording); db.commit()
    audio_path.unlink(missing_ok=True)

