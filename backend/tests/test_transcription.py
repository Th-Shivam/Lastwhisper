from types import SimpleNamespace
import json
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main, search, transcription
from app.config import settings
from app.database import SessionLocal
from app.main import app, cancel_transcription, start_transcription
from app.models import Recording, SearchChunk, SearchIndexState, TranscriptSegment
from app.schemas import RecordingDetailOut, TranscriptionStart


class FakeModel:
    def transcribe(self, _path, **_kwargs):
        return iter([
            SimpleNamespace(start=0.0, end=1.2, text=" Hello there. "),
            SimpleNamespace(start=1.2, end=2.6, text="We meet on Tuesday."),
        ]), SimpleNamespace(language="en", duration=2.6)


def accept_job(_recording_id, _language, before_submit=None):
    if before_submit:
        before_submit()
    return True


@pytest.fixture()
def recording(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4()}.wav"
    audio = b"RIFFnot-real-but-model-is-mocked"
    (settings.storage_path / filename).write_bytes(audio)
    db = SessionLocal()
    item = Recording(
        id=str(uuid.uuid4()), title="Test", original_filename="test.wav",
        relative_audio_path=filename, mime_type="audio/wav", file_size=len(audio),
        transcription_status="not_started",
    )
    db.add(item); db.commit(); db.refresh(item)
    yield db, item, audio
    db.close()


def test_local_inference_persists_timestamped_segments_and_calls_index_hook(recording, monkeypatch):
    db, item, _audio = recording
    indexed = []
    monkeypatch.setattr(transcription, "get_model", lambda: FakeModel())
    monkeypatch.setattr(search, "index_recording", lambda recording_id, _db: indexed.append(recording_id))
    transcription.run_transcription(item.id)
    db.expire_all()
    finished = db.get(Recording, item.id)
    assert finished.transcription_status == "complete"
    assert finished.transcription_language == "en"
    assert finished.transcription_progress == 100
    assert [(segment.start_seconds, segment.end_seconds, segment.text) for segment in finished.segments] == [
        (0.0, 1.2, "Hello there."),
        (1.2, 2.6, "We meet on Tuesday."),
    ]
    payload = RecordingDetailOut.model_validate(finished)
    assert payload.segments[1].start_seconds == 1.2
    assert indexed == [item.id]


def test_inference_failure_is_retryable(recording, monkeypatch):
    db, item, _audio = recording
    monkeypatch.setattr(transcription, "get_model", lambda: (_ for _ in ()).throw(transcription.TranscriptionUnavailable("Model unavailable")))
    transcription.run_transcription(item.id)
    db.expire_all()
    failed = db.get(Recording, item.id)
    assert failed.transcription_status == "failed"
    assert failed.error_message == "Model unavailable"
    monkeypatch.setattr(main, "request_transcription", accept_job)
    queued = start_transcription(item.id, TranscriptionStart(language="en"), db)
    assert queued.transcription_status == "pending"
    assert queued.selected_language == "en"


def test_failed_retranscription_restores_previous_completed_transcript(recording, monkeypatch):
    db, item, _audio = recording
    previous = TranscriptSegment(
        id=str(uuid.uuid4()), recording_id=item.id, position=0,
        start_seconds=0, end_seconds=1, text="Previous transcript",
    )
    item.transcription_status = "complete"
    item.transcription_progress = 100
    item.transcript_text = previous.text
    item.summary = "Previous summary"
    db.add(previous); db.commit()
    monkeypatch.setattr(transcription, "get_model", lambda: (_ for _ in ()).throw(transcription.TranscriptionUnavailable("Model unavailable")))
    transcription.run_transcription(item.id)
    db.expire_all()
    restored = db.get(Recording, item.id)
    assert restored.transcription_status == "complete"
    assert restored.transcription_progress == 100
    assert restored.transcript_text == "Previous transcript"
    assert restored.summary == "Previous summary"
    assert [segment.text for segment in restored.segments] == ["Previous transcript"]


def test_duplicate_start_is_rejected(recording, monkeypatch):
    db, item, _audio = recording
    monkeypatch.setattr(main, "request_transcription", accept_job)
    start_transcription(item.id, None, db)
    with pytest.raises(HTTPException) as exc:
        start_transcription(item.id, None, db)
    assert exc.value.status_code == 409


def test_starting_transcription_preserves_existing_index_until_success(recording, monkeypatch):
    db, item, _audio = recording
    item.transcription_status = "complete"
    db.add(SearchIndexState(
        recording_id=item.id, transcript_hash="old", embedding_model=settings.embedding_model,
        embedding_revision=settings.embedding_revision, embedding_dimension=1,
        chunk_characters=settings.search_chunk_characters,
        chunk_overlap_segments=settings.search_chunk_overlap_segments,
        chunker_version=search.CHUNKER_VERSION, chunk_count=0, status="ready",
    ))
    db.commit()
    monkeypatch.setattr(main, "request_transcription", accept_job)
    start_transcription(item.id, None, db)
    assert db.get(SearchIndexState, item.id) is not None
    assert db.query(SearchChunk).filter_by(recording_id=item.id).count() == 0


def test_cancellation_transitions_to_cancelled(recording):
    db, item, _audio = recording
    item.transcription_status = "processing"; item.transcription_progress = 41; db.commit()
    cancelled = cancel_transcription(item.id, db)
    assert cancelled.transcription_status == "cancelling"
    assert cancelled.transcription_progress == 41


def test_cancellation_after_last_model_segment_wins_over_final_commit(recording, monkeypatch):
    db, item, _audio = recording

    class CancelAtEndModel:
        def transcribe(self, _path, **_kwargs):
            def generated_segments():
                yield SimpleNamespace(start=0.0, end=1.0, text="Do not commit this.")
                with SessionLocal() as cancel_db:
                    cancelled = cancel_db.get(Recording, item.id)
                    cancelled.transcription_status = "cancelled"
                    cancelled.error_message = "Transcription cancelled."
                    cancel_db.commit()
            return generated_segments(), SimpleNamespace(language="en", duration=1.0)

    monkeypatch.setattr(transcription, "get_model", lambda: CancelAtEndModel())
    monkeypatch.setattr(search, "index_recording", lambda *_: pytest.fail("Cancelled transcripts must not be indexed"))
    transcription.run_transcription(item.id)
    db.expire_all()
    cancelled = db.get(Recording, item.id)
    assert cancelled.transcription_status == "cancelled"
    assert cancelled.segments == []


def test_reported_duration_cannot_hide_over_limit_segments(recording, monkeypatch):
    db, item, _audio = recording

    class UnderreportedDurationModel:
        def transcribe(self, _path, **_kwargs):
            return iter([SimpleNamespace(start=0.0, end=6.0, text="Too long")]), SimpleNamespace(language="en", duration=1.0)

    monkeypatch.setattr(settings, "max_processing_duration_seconds", 5)
    monkeypatch.setattr(transcription, "get_model", lambda: UnderreportedDurationModel())
    transcription.run_transcription(item.id)
    db.expire_all()
    failed = db.get(Recording, item.id)
    assert failed.transcription_status == "failed"
    assert "exceeds" in failed.error_message


def test_completed_job_releases_reservation_before_indexing(recording, monkeypatch):
    db, item, _audio = recording
    token = "job-token"
    with transcription.active_lock:
        transcription.active_jobs[item.id] = token
    monkeypatch.setattr(transcription, "get_model", lambda: FakeModel())

    def assert_released(recording_id, _db):
        assert recording_id == item.id
        assert transcription.is_transcription_active(item.id) is False

    monkeypatch.setattr(search, "index_recording", assert_released)
    transcription.run_transcription(item.id, job_token=token)
    db.expire_all()
    assert db.get(Recording, item.id).transcription_status == "complete"


def test_malformed_ollama_output_becomes_unavailable(recording, monkeypatch):
    db, item, _audio = recording
    segment = TranscriptSegment(
        id=str(uuid.uuid4()), recording_id=item.id, position=0,
        start_seconds=0, end_seconds=1, text="Grounded evidence",
    )
    item.transcription_status = "complete"
    item.extraction_status = "pending"
    db.add(segment); db.commit()
    monkeypatch.setattr(settings, "ollama_model", "local-test-model")

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self, _limit):
            malformed = {"summary": ["not", "text"], "key_topics": [], "people": [], "places": [], "dates": [], "action_items": []}
            return json.dumps({"response": json.dumps(malformed)}).encode()

    monkeypatch.setattr(transcription, "urlopen", lambda *_args, **_kwargs: Response())
    transcription.extract_memories(item.id)
    db.expire_all()
    assert db.get(Recording, item.id).extraction_status == "unavailable"


def test_audio_endpoint_returns_original_local_bytes(recording):
    _db, item, audio = recording
    with TestClient(app) as client:
        response = client.get(f"/api/recordings/{item.id}/audio")
    assert response.status_code == 200
    assert response.content == audio
    assert response.headers["content-type"].startswith("audio/wav")
