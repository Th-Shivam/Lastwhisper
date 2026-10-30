"""Local transcription worker. Model imports are delayed so the API still starts without weights."""
from __future__ import annotations
import json
import logging
import math
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from .config import settings
from .database import SessionLocal
from .models import Recording, SavedMemory, SearchChunk, SearchIndexState, TranscriptSegment

logger = logging.getLogger(__name__)

executor: ThreadPoolExecutor | None = None
active_jobs: dict[str, str] = {}
active_lock = threading.Lock()
finalization_lock = threading.Lock()
model_lock = threading.Lock()
shutdown_requested = threading.Event()
_whisper_model = None
_whisper_model_key: tuple[str, str | None, str, str, str | None, bool] | None = None


class TranscriptionUnavailable(RuntimeError):
    pass


class TranscriptionQueueFull(RuntimeError):
    pass


def audio_path(recording: Recording) -> Path:
    path = (settings.storage_path / recording.relative_audio_path).resolve()
    if settings.storage_path not in path.parents or not path.is_file():
        raise TranscriptionUnavailable("The local audio file is unavailable.")
    if path.stat().st_size == 0:
        raise TranscriptionUnavailable("The audio file is empty.")
    return path


def get_model():
    global _whisper_model, _whisper_model_key
    key = (
        settings.whisper_model,
        settings.whisper_revision,
        settings.whisper_device,
        settings.whisper_compute_type,
        str(settings.whisper_cache_path) if settings.whisper_cache_path else None,
        settings.whisper_local_files_only,
    )
    with model_lock:
        if _whisper_model is not None and _whisper_model_key == key:
            return _whisper_model
        _whisper_model = None
        _whisper_model_key = key
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionUnavailable("Local transcription is not installed. Install the backend dependencies.") from exc
        try:
            _whisper_model = WhisperModel(
                settings.whisper_model,
                revision=settings.whisper_revision,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
                download_root=str(settings.whisper_cache_path) if settings.whisper_cache_path else None,
                local_files_only=settings.whisper_local_files_only,
            )
            return _whisper_model
        except Exception as exc:
            raise TranscriptionUnavailable("The selected local Whisper model is unavailable or cannot run with this device configuration.") from exc


def start_worker() -> None:
    global executor
    with active_lock:
        if executor is None:
            shutdown_requested.clear()
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lastwhisper-transcription")


def shutdown_worker() -> None:
    global executor, _whisper_model, _whisper_model_key
    shutdown_requested.set()
    with active_lock:
        current = executor
        executor = None
        active_jobs.clear()
    if current is not None:
        current.shutdown(wait=False, cancel_futures=True)
    with model_lock:
        _whisper_model = None
        _whisper_model_key = None


def reset_model_cache() -> None:
    global _whisper_model, _whisper_model_key
    with model_lock:
        _whisper_model = None
        _whisper_model_key = None


def _get_executor() -> ThreadPoolExecutor:
    start_worker()
    assert executor is not None
    return executor


def _ollama_generate_url() -> str:
    parsed = urlparse(settings.ollama_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise TranscriptionUnavailable("OLLAMA_URL must be a valid HTTP URL.")
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if not settings.allow_remote_ollama and parsed.hostname.lower() not in local_hosts:
        raise TranscriptionUnavailable("Remote Ollama endpoints are disabled. Use localhost or explicitly enable ALLOW_REMOTE_OLLAMA.")
    return f"{settings.ollama_url.rstrip('/')}/api/generate"


def request_transcription(
    recording_id: str,
    language: str | None = None,
    before_submit: Callable[[], None] | None = None,
) -> bool:
    """Reserve a recording ID, persist pending state, then submit its worker."""
    with active_lock:
        if recording_id in active_jobs:
            return False
        if len(active_jobs) >= settings.max_transcription_jobs:
            raise TranscriptionQueueFull("The local transcription queue is full. Wait for an active job to finish and retry.")
        job_token = str(uuid.uuid4())
        active_jobs[recording_id] = job_token
    try:
        if before_submit:
            before_submit()
        _get_executor().submit(run_transcription, recording_id, language, job_token)
    except Exception:
        with active_lock:
            if active_jobs.get(recording_id) == job_token:
                active_jobs.pop(recording_id, None)
        raise
    return True


def is_transcription_active(recording_id: str) -> bool:
    with active_lock:
        return recording_id in active_jobs


def _release_active_job(recording_id: str, job_token: str | None) -> None:
    if job_token is None:
        return
    with active_lock:
        if active_jobs.get(recording_id) == job_token:
            active_jobs.pop(recording_id, None)


def mark_transcription_cancelled(recording_id: str, db: Session) -> Recording | None:
    """Cancel without racing the worker's final transcript replacement commit."""
    with finalization_lock:
        recording = db.get(Recording, recording_id)
        if recording is None:
            return None
        db.refresh(recording)
        if recording.transcription_status not in {"pending", "processing"}:
            return None
        recording.transcription_status = "cancelling"
        recording.error_message = "Cancellation requested. The local worker is stopping."
        db.commit()
        db.refresh(recording)
        return recording


def run_transcription(
    recording_id: str,
    language: str | None = None,
    job_token: str | None = None,
) -> None:
    db = SessionLocal()
    try:
        with finalization_lock:
            recording = db.get(Recording, recording_id)
            if not recording or recording.transcription_status in {"cancelled", "cancelling"}:
                return
            recording.transcription_status, recording.transcription_progress = "processing", 1
            recording.error_message = None
            db.commit()
        if shutdown_requested.is_set():
            recording.transcription_status = "cancelling"
            recording.error_message = "Application shutdown interrupted local processing."
            db.commit()
            return
        path = audio_path(recording)
        model = get_model()
        kwargs = {"beam_size": 5, "vad_filter": True}
        if language:
            kwargs["language"] = language
        segments, info = model.transcribe(str(path), **kwargs)
        rows, all_text, last_end = [], [], 0.0
        estimated_duration = float(getattr(info, "duration", 0) or 0)
        if not math.isfinite(estimated_duration) or estimated_duration < 0:
            raise TranscriptionUnavailable("The transcription model returned an invalid audio duration.")
        if estimated_duration > settings.max_processing_duration_seconds:
            raise TranscriptionUnavailable("This recording exceeds the configured processing duration limit.")
        for index, segment in enumerate(segments):
            db.refresh(recording)
            if recording.transcription_status != "processing" or shutdown_requested.is_set():
                if shutdown_requested.is_set() and recording.transcription_status == "processing":
                    recording.transcription_status = "cancelling"
                    recording.error_message = "Application shutdown interrupted local processing."
                    db.commit()
                return
            start, end, text = float(segment.start), float(segment.end), segment.text.strip()
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
                raise TranscriptionUnavailable("The transcription model returned invalid source timestamps.")
            if end > settings.max_processing_duration_seconds:
                raise TranscriptionUnavailable("This recording exceeds the configured processing duration limit.")
            if text:
                rows.append(TranscriptSegment(id=str(uuid.uuid4()), recording_id=recording.id, position=index, start_seconds=start, end_seconds=end, text=text))
                all_text.append(text); last_end = max(last_end, end)
                if estimated_duration:
                    recording.transcription_progress = min(95, max(2, int((end / estimated_duration) * 95)))
                db.commit()
        if not rows:
            raise TranscriptionUnavailable("No speech could be detected in this audio.")
        measured_duration = max(estimated_duration, last_end)
        if measured_duration > settings.max_processing_duration_seconds:
            raise TranscriptionUnavailable("This recording exceeds the configured processing duration limit.")
        actual_duration = int(round(measured_duration))
        with finalization_lock:
            db.refresh(recording)
            if recording.transcription_status != "processing":
                return
            # Re-transcription replaces source segment IDs, so dependent snapshots
            # are removed rather than left with invalid evidence attribution.
            db.execute(delete(SavedMemory).where(SavedMemory.recording_id == recording.id))
            db.execute(delete(SearchChunk).where(SearchChunk.recording_id == recording.id))
            db.execute(delete(SearchIndexState).where(SearchIndexState.recording_id == recording.id))
            db.execute(delete(TranscriptSegment).where(TranscriptSegment.recording_id == recording.id))
            db.add_all(rows)
            recording.transcript_text = " ".join(all_text)
            recording.transcription_language = getattr(info, "language", None)
            recording.duration_seconds = actual_duration or recording.duration_seconds
            recording.transcription_status, recording.transcription_progress = "complete", 100
            recording.extraction_status = "pending" if settings.ollama_model else "unavailable"
            recording.summary = None
            recording.extracted_memories = None
            db.commit()
        # Transcript persistence is complete. Indexing and optional enrichment are
        # guarded against transcript changes and should not block user actions.
        _release_active_job(recording_id, job_token)
        # Index failures do not invalidate a completed transcript; search can rebuild later.
        if not shutdown_requested.is_set():
            try:
                from .search import index_recording
                index_recording(recording_id, db)
            except Exception as exc:
                logger.warning("Search indexing failed for recording %s: %s", recording_id, exc)
        if settings.ollama_model and not shutdown_requested.is_set():
            try:
                extract_memories(recording_id)
            except Exception as exc:
                logger.warning("Local enrichment failed for recording %s: %s", recording_id, exc)
    except Exception as exc:
        db.rollback()
        recording = db.get(Recording, recording_id)
        if recording and recording.transcription_status not in {"cancelled", "cancelling"}:
            has_previous_transcript = bool(db.scalar(
                select(TranscriptSegment.id).where(TranscriptSegment.recording_id == recording_id).limit(1)
            ))
            recording.transcription_status = "complete" if has_previous_transcript else "failed"
            recording.transcription_progress = 100 if has_previous_transcript else 0
            reason = str(exc) if isinstance(exc, TranscriptionUnavailable) else "Local transcription failed. Check the model, audio file, and available system resources."
            recording.error_message = f"Re-transcription failed; the previous transcript was kept. {reason}" if has_previous_transcript else reason
            db.commit()
    finally:
        try:
            db.rollback()
            recording = db.get(Recording, recording_id)
            if recording and recording.transcription_status == "cancelling":
                has_previous_transcript = bool(db.scalar(
                    select(TranscriptSegment.id).where(TranscriptSegment.recording_id == recording_id).limit(1)
                ))
                recording.transcription_status = "complete" if has_previous_transcript else "cancelled"
                recording.transcription_progress = 100 if has_previous_transcript else 0
                recording.error_message = "Re-transcription cancelled; the previous transcript was kept." if has_previous_transcript else "Transcription cancelled."
                db.commit()
        except Exception:
            db.rollback()
        db.close()
        _release_active_job(recording_id, job_token)


def extract_memories(recording_id: str) -> None:
    db = SessionLocal()
    recording: Recording | None = None
    source_segment_ids: tuple[str, ...] = ()
    try:
        recording = db.get(Recording, recording_id)
        if not recording or recording.transcription_status != "complete" or not settings.ollama_model:
            return
        segments = list(recording.segments)
        source_segment_ids = tuple(segment.id for segment in segments)
        source = "\n".join(f"[{s.id}] {s.text}" for s in segments)
        prompt = ("Return strict JSON only: {summary:string,key_topics:string[],people:array,places:array,dates:array,action_items:array}. "
                  "Each entity/action must be {value:string,segment_ids:string[]}. Only include facts explicitly present in the transcript; omit uncertain items. "
                  f"Transcript:\n{source}")
        request = Request(_ollama_generate_url(), data=json.dumps({"model": settings.ollama_model, "prompt": prompt, "stream": False, "format": "json"}).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=90) as response:
            raw_response = response.read(settings.ollama_max_response_bytes + 1)
            if len(raw_response) > settings.ollama_max_response_bytes:
                raise ValueError("Ollama response exceeded the configured size limit.")
            result = json.loads(json.loads(raw_response.decode())["response"])
        if not isinstance(result, dict):
            raise ValueError("Ollama enrichment must be a JSON object.")
        summary = result.get("summary")
        if summary is not None and not isinstance(summary, str):
            raise ValueError("Ollama summary must be text.")
        topics = result.get("key_topics", [])
        if not isinstance(topics, list) or not all(isinstance(topic, str) for topic in topics):
            raise ValueError("Ollama key_topics must be a string list.")
        sanitized = {"summary": summary.strip() or None if summary else None, "key_topics": topics}
        allowed = {s.id for s in segments}
        for key in ("people", "places", "dates", "action_items"):
            values = result.get(key, [])
            if not isinstance(values, list):
                raise ValueError(f"Ollama {key} must be a list.")
            sanitized[key] = [
                item for item in values
                if isinstance(item, dict)
                and item.get("value")
                and isinstance(item.get("segment_ids"), list)
                and item["segment_ids"]
                and all(isinstance(segment_id, str) for segment_id in item["segment_ids"])
                and set(item["segment_ids"]) <= allowed
            ]
        with finalization_lock:
            db.refresh(recording)
            current_segment_ids = tuple(db.scalars(
                select(TranscriptSegment.id)
                .where(TranscriptSegment.recording_id == recording_id)
                .order_by(TranscriptSegment.position)
            ))
            if recording.transcription_status != "complete" or current_segment_ids != source_segment_ids:
                return
            recording.summary = sanitized["summary"]
            recording.extracted_memories = json.dumps(sanitized)
            recording.extraction_status = "complete"
            db.commit()
    except Exception as exc:
        logger.warning("Local enrichment became unavailable for recording %s (%s)", recording_id, type(exc).__name__)
        db.rollback()
        try:
            recording = db.get(Recording, recording_id)
            if recording:
                with finalization_lock:
                    db.refresh(recording)
                    current_segment_ids = tuple(db.scalars(
                        select(TranscriptSegment.id)
                        .where(TranscriptSegment.recording_id == recording_id)
                        .order_by(TranscriptSegment.position)
                    ))
                    if recording.transcription_status == "complete" and current_segment_ids == source_segment_ids:
                        recording.extraction_status = "unavailable"
                        db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()
