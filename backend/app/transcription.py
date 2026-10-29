"""Local transcription worker. Model imports are delayed so the API still starts without weights."""
from __future__ import annotations
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen
from sqlalchemy import delete
from .config import settings
from .database import SessionLocal
from .models import Recording, TranscriptSegment

executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lastwhisper-transcription")
active_jobs: set[str] = set()
active_lock = threading.Lock()


class TranscriptionUnavailable(RuntimeError):
    pass


def audio_path(recording: Recording) -> Path:
    path = (settings.storage_path / recording.relative_audio_path).resolve()
    if settings.storage_path not in path.parents or not path.is_file():
        raise TranscriptionUnavailable("The local audio file is unavailable.")
    if path.stat().st_size == 0:
        raise TranscriptionUnavailable("The audio file is empty.")
    return path


def get_model():
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionUnavailable("Local transcription is not installed. Install the backend dependencies.") from exc
    try:
        return WhisperModel(settings.whisper_model, device=settings.whisper_device, compute_type=settings.whisper_compute_type, download_root=settings.whisper_download_root)
    except Exception as exc:
        raise TranscriptionUnavailable("The selected local Whisper model is unavailable or cannot run with this device configuration.") from exc


def request_transcription(recording_id: str, language: str | None = None) -> bool:
    with active_lock:
        if recording_id in active_jobs:
            return False
        active_jobs.add(recording_id)
    executor.submit(run_transcription, recording_id, language)
    return True


def run_transcription(recording_id: str, language: str | None = None) -> None:
    db = SessionLocal()
    try:
        recording = db.get(Recording, recording_id)
        if not recording or recording.transcription_status == "cancelled":
            return
        recording.transcription_status, recording.transcription_progress = "processing", 1
        recording.error_message = None
        db.commit()
        path = audio_path(recording)
        model = get_model()
        kwargs = {"beam_size": 5, "vad_filter": True}
        if language:
            kwargs["language"] = language
        segments, info = model.transcribe(str(path), **kwargs)
        rows, all_text, last_end = [], [], 0.0
        for index, segment in enumerate(segments):
            db.refresh(recording)
            if recording.transcription_status == "cancelled":
                return
            start, end, text = float(segment.start), float(segment.end), segment.text.strip()
            if text:
                rows.append(TranscriptSegment(id=str(uuid.uuid4()), recording_id=recording.id, position=index, start_seconds=start, end_seconds=end, text=text))
                all_text.append(text); last_end = max(last_end, end)
                recording.transcription_progress = min(95, max(2, int((end / max(last_end, 1)) * 95)))
                db.commit()
        if not rows:
            raise TranscriptionUnavailable("No speech could be detected in this audio.")
        actual_duration = int(round(getattr(info, "duration", 0) or last_end))
        if actual_duration > settings.max_processing_duration_seconds:
            raise TranscriptionUnavailable("This recording exceeds the configured processing duration limit.")
        db.execute(delete(TranscriptSegment).where(TranscriptSegment.recording_id == recording.id))
        db.add_all(rows)
        recording.transcript_text = " ".join(all_text)
        recording.transcription_language = getattr(info, "language", None)
        recording.duration_seconds = actual_duration or recording.duration_seconds
        recording.transcription_status, recording.transcription_progress = "complete", 100
        recording.extraction_status = "pending" if settings.ollama_model else "unavailable"
        db.commit()
        if settings.ollama_model:
            extract_memories(recording_id)
    except Exception as exc:
        db.rollback()
        recording = db.get(Recording, recording_id)
        if recording and recording.transcription_status != "cancelled":
            recording.transcription_status, recording.transcription_progress = "failed", 0
            recording.error_message = str(exc) if isinstance(exc, TranscriptionUnavailable) else "Local transcription failed. Check the model, audio file, and available system resources."
            db.commit()
    finally:
        db.close()
        with active_lock:
            active_jobs.discard(recording_id)


def extract_memories(recording_id: str) -> None:
    db = SessionLocal()
    try:
        recording = db.get(Recording, recording_id)
        if not recording or not settings.ollama_model:
            return
        segments = list(recording.segments)
        source = "\n".join(f"[{s.id}] {s.text}" for s in segments)
        prompt = ("Return strict JSON only: {summary:string,key_topics:string[],people:array,places:array,dates:array,action_items:array}. "
                  "Each entity/action must be {value:string,segment_ids:string[]}. Only include facts explicitly present in the transcript; omit uncertain items. "
                  f"Transcript:\n{source}")
        request = Request(f"{settings.ollama_url.rstrip('/')}/api/generate", data=json.dumps({"model": settings.ollama_model, "prompt": prompt, "stream": False, "format": "json"}).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=90) as response:
            result = json.loads(json.loads(response.read().decode())["response"])
        allowed = {s.id for s in segments}
        for key in ("people", "places", "dates", "action_items"):
            result[key] = [item for item in result.get(key, []) if isinstance(item, dict) and item.get("value") and set(item.get("segment_ids", [])) <= allowed]
        recording.summary = result.get("summary") or None
        recording.extracted_memories = json.dumps(result)
        recording.extraction_status = "complete"
        db.commit()
    except (URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError):
        if recording := db.get(Recording, recording_id):
            recording.extraction_status = "unavailable"; db.commit()
    finally:
        db.close()
