from pathlib import Path
from types import SimpleNamespace
import uuid
import pytest
from fastapi import HTTPException
from app.config import settings
from app.database import SessionLocal, migrate_schema
from app.main import start_transcription
from app.models import Recording, TranscriptSegment
from app.schemas import RecordingDetailOut, TranscriptionStart
from app import transcription


class FakeModel:
    def transcribe(self, _path, **_kwargs):
        return iter([
            SimpleNamespace(start=0.0, end=1.2, text=" Hello there. "),
            SimpleNamespace(start=1.2, end=2.6, text="We meet on Tuesday."),
        ]), SimpleNamespace(language="en", duration=2.6)


@pytest.fixture()
def recording(tmp_path, monkeypatch):
    migrate_schema()
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    settings.storage_path.mkdir(parents=True)
    filename = f"{uuid.uuid4()}.wav"
    (settings.storage_path / filename).write_bytes(b"RIFFnot-real-but-model-is-mocked")
    db = SessionLocal()
    item = Recording(id=str(uuid.uuid4()), title="Test", original_filename="test.wav", relative_audio_path=filename, mime_type="audio/wav", file_size=28, transcription_status="not_started")
    db.add(item); db.commit(); db.refresh(item)
    yield db, item
    db.delete(item); db.commit(); db.close()


def test_local_inference_persists_timestamped_segments(recording, monkeypatch):
    db, item = recording
    monkeypatch.setattr(transcription, "get_model", lambda: FakeModel())
    transcription.run_transcription(item.id)
    db.expire_all(); finished = db.get(Recording, item.id)
    assert finished.transcription_status == "complete"
    assert finished.transcription_language == "en"
    assert finished.transcription_progress == 100
    assert [(s.start_seconds, s.end_seconds, s.text) for s in finished.segments] == [(0.0, 1.2, "Hello there."), (1.2, 2.6, "We meet on Tuesday.")]
    payload = RecordingDetailOut.model_validate(finished)
    assert payload.segments[1].start_seconds == 1.2


def test_inference_failure_is_retryable(recording, monkeypatch):
    db, item = recording
    monkeypatch.setattr(transcription, "get_model", lambda: (_ for _ in ()).throw(transcription.TranscriptionUnavailable("Model unavailable")))
    transcription.run_transcription(item.id)
    db.expire_all(); failed = db.get(Recording, item.id)
    assert failed.transcription_status == "failed"
    assert failed.error_message == "Model unavailable"
    monkeypatch.setattr(transcription, "request_transcription", lambda *_: True)
    queued = start_transcription(item.id, TranscriptionStart(language="en"), db)
    assert queued.transcription_status == "pending"
    assert queued.selected_language == "en"


def test_duplicate_start_is_rejected(recording, monkeypatch):
    db, item = recording
    monkeypatch.setattr(transcription, "request_transcription", lambda *_: True)
    start_transcription(item.id, None, db)
    with pytest.raises(HTTPException) as exc:
        start_transcription(item.id, None, db)
    assert exc.value.status_code == 409
