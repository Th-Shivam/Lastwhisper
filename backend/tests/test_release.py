import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app import main, transcription
from app.config import BACKEND_DIR, Settings, settings
from app.database import SessionLocal
from app.main import app
from app.models import Recording, SavedMemory, SearchIndexState, TranscriptSegment
from app.search import CHUNKER_VERSION


def wav_bytes() -> bytes:
    samples = b"\x00\x00" * 16
    return (
        b"RIFF" + (36 + len(samples)).to_bytes(4, "little") + b"WAVE"
        + b"fmt " + (16).to_bytes(4, "little") + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little") + (16000).to_bytes(4, "little")
        + (32000).to_bytes(4, "little") + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little") + b"data" + len(samples).to_bytes(4, "little") + samples
    )


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_upload_validates_signature_permissions_and_audio_response(client):
    malformed = client.post(
        "/api/recordings/import",
        files={"file": ("renamed.wav", b"not wave audio", "audio/wav")},
    )
    assert malformed.status_code == 422

    audio = wav_bytes()
    uploaded = client.post(
        "/api/recordings/import",
        files={"file": ("C:\\fakepath\\private.wav", audio, "application/octet-stream")},
    )
    assert uploaded.status_code == 201
    payload = uploaded.json()
    assert payload["original_filename"] == "private.wav"
    assert payload["mime_type"] == "audio/wav"
    stored_path = settings.storage_path / f"{payload['id']}.wav"
    # Storage names are independent UUIDs, so resolve through the database.
    with SessionLocal() as db:
        recording = db.get(Recording, payload["id"])
        stored_path = settings.storage_path / recording.relative_audio_path
    assert stored_path.read_bytes() == audio
    assert stored_path.stat().st_mode & 0o077 == 0

    response = client.get(f"/api/recordings/{payload['id']}/audio")
    assert response.status_code == 200
    assert response.content == audio
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.headers["cache-control"] == "private, no-store"
    assert "content-disposition" not in response.headers
    ranged = client.get(f"/api/recordings/{payload['id']}/audio", headers={"Range": "bytes=0-3"})
    assert ranged.status_code == 206
    assert ranged.content == b"RIFF"

    assert client.delete(f"/api/recordings/{payload['id']}").status_code == 204
    assert not stored_path.exists()


def test_oversized_content_length_is_rejected_before_multipart_parsing(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)
    response = client.post(
        "/api/recordings/import",
        content=b"ignored",
        headers={"Content-Type": "multipart/form-data; boundary=x", "Content-Length": str(3 * 1024 * 1024)},
    )
    assert response.status_code == 413
    multipart_prefix = b'--x\r\nContent-Disposition: form-data; name="file"; filename="large.wav"\r\nContent-Type: audio/wav\r\n\r\n'
    multipart_suffix = b"\r\n--x--\r\n"
    chunked = client.post(
        "/api/recordings/import",
        content=iter([multipart_prefix, b"x" * (700 * 1024), b"y" * (700 * 1024), b"z" * (700 * 1024), multipart_suffix]),
        headers={"Content-Type": "multipart/form-data; boundary=x", "Transfer-Encoding": "chunked"},
    )
    # Starlette may reject the oversized streaming part before the outer byte
    # counter; either response prevents it reaching persistent storage.
    assert chunked.status_code in {400, 413}, chunked.text


def test_readiness_and_trusted_host_defaults(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/ready").json()["status"] == "ready"
    assert client.get("/api/health", headers={"Host": "public.example"}).status_code == 400
    blocked = client.post(
        "/api/search/rebuild",
        json={"confirm": True},
        headers={"Origin": "https://malicious.example"},
    )
    assert blocked.status_code == 403


def test_retranscription_requires_confirmation_and_preserves_data_until_success(client, monkeypatch):
    recording_id = str(uuid.uuid4())
    filename = f"{recording_id}.wav"
    (settings.storage_path / filename).write_bytes(wav_bytes())
    with SessionLocal() as db:
        recording = Recording(
            id=recording_id, title="Existing", original_filename="existing.wav",
            relative_audio_path=filename, mime_type="audio/wav", file_size=len(wav_bytes()),
            transcription_status="complete", transcription_progress=100,
            transcript_text="Original transcript", summary="Original summary",
            extraction_status="complete",
        )
        segment = TranscriptSegment(
            id=str(uuid.uuid4()), recording_id=recording_id, position=0,
            start_seconds=0, end_seconds=2, text="Original transcript",
        )
        db.add_all([recording, segment]); db.flush()
        memory = SavedMemory(
            id=str(uuid.uuid4()), recording_id=recording_id, segment_id=segment.id,
            title="Keep me", note="Personal note", source_text=segment.text,
            source_start_seconds=0, source_end_seconds=2,
        )
        state = SearchIndexState(
            recording_id=recording_id, transcript_hash="old", embedding_model=settings.embedding_model,
            embedding_revision=settings.embedding_revision, embedding_dimension=2,
            chunk_characters=settings.search_chunk_characters,
            chunk_overlap_segments=settings.search_chunk_overlap_segments,
            chunker_version=CHUNKER_VERSION, chunk_count=0, status="ready",
        )
        db.add_all([memory, state]); db.commit()

    refused = client.post(f"/api/recordings/{recording_id}/transcribe", json={})
    assert refused.status_code == 409
    assert "replace_existing=true" in refused.json()["detail"]

    def accept_job(_recording_id, _language, before_submit=None):
        if before_submit:
            before_submit()
        return True

    monkeypatch.setattr(main, "request_transcription", accept_job)
    accepted = client.post(
        f"/api/recordings/{recording_id}/transcribe",
        json={"replace_existing": True},
    )
    assert accepted.status_code == 202
    with SessionLocal() as db:
        current = db.get(Recording, recording_id)
        assert current.transcription_status == "pending"
        assert current.summary == "Original summary"
        assert db.get(SearchIndexState, recording_id) is not None
        assert db.query(SavedMemory).filter_by(recording_id=recording_id).count() == 1


def test_remote_ollama_is_rejected_by_default(monkeypatch):
    monkeypatch.setattr(settings, "ollama_url", "https://example.com")
    monkeypatch.setattr(settings, "allow_remote_ollama", False)
    with pytest.raises(transcription.TranscriptionUnavailable, match="Remote Ollama"):
        transcription._ollama_generate_url()


def test_paths_are_backend_anchored_without_breaking_sqlite_memory_urls():
    memory = Settings(database_url="sqlite:///:memory:", storage_dir="./private-audio", _env_file=None)
    relative = Settings(database_url="sqlite:///./private.sqlite3", storage_dir="./private-audio", _env_file=None)
    assert memory.database_url == "sqlite:///:memory:"
    assert relative.database_path == BACKEND_DIR / "private.sqlite3"
    assert relative.storage_path == BACKEND_DIR / "private-audio"


def test_startup_recovers_interrupted_transcription_extraction_and_index():
    previous_id = str(uuid.uuid4())
    new_id = str(uuid.uuid4())
    with SessionLocal() as db:
        previous = Recording(
            id=previous_id, title="Previous", original_filename="previous.wav",
            relative_audio_path="previous.wav", mime_type="audio/wav", file_size=1,
            transcription_status="processing", transcription_progress=45,
            extraction_status="pending",
        )
        new = Recording(
            id=new_id, title="New", original_filename="new.wav",
            relative_audio_path="new.wav", mime_type="audio/wav", file_size=1,
            transcription_status="pending", transcription_progress=0,
            extraction_status="unavailable",
        )
        segment = TranscriptSegment(
            id=str(uuid.uuid4()), recording_id=previous_id, position=0,
            start_seconds=0, end_seconds=1, text="Existing transcript",
        )
        state = SearchIndexState(
            recording_id=previous_id, transcript_hash="hash", embedding_model=settings.embedding_model,
            embedding_revision=settings.embedding_revision, embedding_dimension=2,
            chunk_characters=settings.search_chunk_characters,
            chunk_overlap_segments=settings.search_chunk_overlap_segments,
            chunker_version=CHUNKER_VERSION, chunk_count=0, status="indexing",
        )
        db.add_all([previous, new, segment, state]); db.commit()

    main._initialize_app()
    with SessionLocal() as db:
        previous = db.get(Recording, previous_id)
        new = db.get(Recording, new_id)
        assert (previous.transcription_status, previous.transcription_progress) == ("complete", 100)
        assert previous.extraction_status == "unavailable"
        assert (new.transcription_status, new.transcription_progress) == ("failed", 0)
        assert db.get(SearchIndexState, previous_id).status == "failed"


def test_startup_reconciles_crash_staged_uploads_and_deletions():
    recording_id = str(uuid.uuid4())
    stored_name = f"{uuid.uuid4()}.wav"
    upload_stage = settings.storage_path / f"{main.UPLOAD_STAGE_PREFIX}{stored_name}"
    upload_stage.write_bytes(wav_bytes())
    with SessionLocal() as db:
        db.add(Recording(
            id=recording_id, title="Staged", original_filename="staged.wav",
            relative_audio_path=stored_name, mime_type="audio/wav", file_size=len(wav_bytes()),
            transcription_status="not_started",
        ))
        db.commit()
        main._reconcile_file_operations(db)
    destination = settings.storage_path / stored_name
    assert destination.exists() and not upload_stage.exists()

    tombstone = settings.storage_path / f"{main.DELETE_STAGE_PREFIX}{recording_id}--{stored_name}"
    destination.replace(tombstone)
    with SessionLocal() as db:
        main._reconcile_file_operations(db)
    assert destination.exists() and not tombstone.exists()

    destination.replace(tombstone)
    with SessionLocal() as db:
        db.delete(db.get(Recording, recording_id)); db.commit()
        main._reconcile_file_operations(db)
    assert not destination.exists() and not tombstone.exists()
