import json
import math
import sqlite3
import sys
import threading
import types
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select

from app import search
from app.config import settings
from app.database import SessionLocal, migrate_schema
from app.main import app
from app.models import Recording, SavedMemory, SearchChunk, SearchIndexState, TranscriptSegment


class FakeEmbedder:
    def __init__(self):
        self.calls = 0

    def encode(self, texts, normalize_embeddings=True):
        self.calls += 1
        vectors = []
        for value in texts:
            text = value.lower()
            vector = [
                float(any(word in text for word in ("internship", "career", "interview"))),
                float(any(word in text for word in ("laptop", "computer", "hardware"))),
                float(any(word in text for word in ("paris", "holiday", "travel"))),
                0.0,
            ]
            if not any(vector):
                vector[-1] = 1.0
            vectors.append(vector)
        return vectors


@pytest.fixture()
def fake_embedder(monkeypatch):
    embedder = FakeEmbedder()
    monkeypatch.setattr(search, "get_embedder", lambda: embedder)
    return embedder


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def create_recording(db, title="Journal", segments=None):
    recording_id = str(uuid.uuid4())
    filename = f"{recording_id}.wav"
    (settings.storage_path / filename).write_bytes(b"RIFFtest-audio")
    recording = Recording(
        id=recording_id,
        title=title,
        original_filename=f"{title.lower()}.wav",
        relative_audio_path=filename,
        mime_type="audio/wav",
        file_size=14,
        transcription_status="complete",
        transcription_language="en",
        transcript_text=" ".join(text for _start, _end, text in (segments or [])),
    )
    db.add(recording)
    db.flush()
    rows = []
    for position, (start, end, text) in enumerate(segments or []):
        rows.append(TranscriptSegment(
            id=str(uuid.uuid4()),
            recording_id=recording.id,
            position=position,
            start_seconds=start,
            end_seconds=end,
            text=text,
        ))
    db.add_all(rows)
    db.commit()
    db.refresh(recording)
    return recording


def test_chunk_creation_overlap_and_long_segment_split():
    segments = [
        TranscriptSegment(id="s1", recording_id="r", position=0, start_seconds=0, end_seconds=4, text="alpha words here"),
        TranscriptSegment(id="s2", recording_id="r", position=1, start_seconds=4, end_seconds=8, text="beta words here"),
        TranscriptSegment(id="s3", recording_id="r", position=2, start_seconds=8, end_seconds=12, text="gamma words here"),
    ]
    chunks = search.chunks_for_segments(segments, max_characters=34, overlap_segments=1)
    assert len(chunks) >= 2
    assert all(len(chunk.text) <= 34 for chunk in chunks)
    assert set(chunks[0].segment_ids).intersection(chunks[1].segment_ids) == {"s2"}
    assert chunks[0].start_seconds == 0

    long_segment = TranscriptSegment(
        id="long", recording_id="r", position=0, start_seconds=10, end_seconds=20,
        text="a-very-long-unbroken-token-that-must-be-split-safely",
    )
    long_chunks = search.chunks_for_segments([long_segment], max_characters=16, overlap_segments=0)
    assert len(long_chunks) > 1
    assert all(len(chunk.text) <= 16 and chunk.segment_ids == ("long",) for chunk in long_chunks)
    assert long_chunks[0].start_seconds == 10
    assert long_chunks[-1].end_seconds == 20


def test_embedding_normalization_serialization_and_cosine_validation():
    normalized = search.normalize_vector([3.0, 4.0])
    assert math.sqrt(sum(value * value for value in normalized)) == pytest.approx(1.0)
    assert search.unpack(search.pack(normalized), 2) == pytest.approx(normalized)
    assert search.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert search.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    with pytest.raises(ValueError):
        search.cosine([1.0], [1.0, 0.0])
    with pytest.raises(ValueError):
        search.unpack(b"bad")
    with pytest.raises(ValueError):
        search.normalize_vector([0.0, 0.0])


def test_lazy_embedder_is_cached_without_real_model_import(monkeypatch):
    constructions = []

    class StubSentenceTransformer:
        def __init__(self, model_name, **kwargs):
            constructions.append((model_name, kwargs))

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = StubSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    search.reset_embedding_cache()
    first = search.get_embedder()
    second = search.get_embedder()
    assert first is second
    assert len(constructions) == 1
    assert constructions[0][1]["device"] == settings.embedding_device
    assert constructions[0][1]["local_files_only"] is True


def test_mocked_embedding_ranking_attribution_and_threshold(fake_embedder, monkeypatch):
    with SessionLocal() as db:
        career = create_recording(db, "Career note", [(3, 7, "I discussed my internship interview and career plans.")])
        laptop = create_recording(db, "Hardware note", [(11, 15, "I need to replace my old laptop computer.")])
        search.index_recording(career.id, db)
        search.index_recording(laptop.id, db)

        matches = search.search(db, "What happened with my internship interview?")
        assert [match.recording.id for match in matches] == [career.id]
        assert matches[0].chunk.primary_segment_id == career.segments[0].id
        assert matches[0].segment_ids == (career.segments[0].id,)
        assert matches[0].chunk.start_seconds == 3
        assert matches[0].chunk.end_seconds == 7

        monkeypatch.setattr(settings, "search_min_similarity", 0.8)
        assert search.search(db, "internship laptop") == []
        monkeypatch.setattr(settings, "search_min_similarity", 0.7)
        assert len(search.search(db, "internship laptop")) == 2


def test_empty_transcript_and_unavailable_model_are_safe(fake_embedder, monkeypatch):
    with SessionLocal() as db:
        empty = create_recording(db, "Silence", [(0, 2, "   ")])
        result = search.index_recording(empty.id, db)
        assert result.status == "empty"
        assert result.indexed_chunks == 0
        assert db.get(SearchIndexState, empty.id).status == "empty"

        unavailable = create_recording(db, "Unavailable", [(0, 2, "career change")])
        monkeypatch.setattr(search, "get_embedder", lambda: (_ for _ in ()).throw(search.SearchUnavailable("Model cache missing")))
        with pytest.raises(search.SearchUnavailable, match="Model cache missing"):
            search.index_recording(unavailable.id, db)
        assert db.get(SearchIndexState, unavailable.id).status == "failed"
        assert db.scalar(select(func.count(SearchChunk.id)).where(SearchChunk.recording_id == unavailable.id)) == 0


def test_malformed_stored_vector_is_skipped(fake_embedder):
    with SessionLocal() as db:
        recording = create_recording(db, segments=[(0, 3, "My internship interview went well.")])
        search.index_recording(recording.id, db)
        chunk = db.scalar(select(SearchChunk).where(SearchChunk.recording_id == recording.id))
        chunk.embedding = b"bad"
        db.commit()
        assert search.search(db, "internship") == []


def test_cross_record_chunk_attribution_is_rejected(client, fake_embedder):
    with SessionLocal() as db:
        first = create_recording(db, "First", [(0, 3, "career interview")])
        second = create_recording(db, "Second", [(4, 7, "laptop hardware")])
        search.index_recording(first.id, db)
        chunk = db.scalar(select(SearchChunk).where(SearchChunk.recording_id == first.id))
        chunk.segment_ids = json.dumps([first.segments[0].id, second.segments[0].id])
        chunk_id = chunk.id
        db.commit()
        assert search.search(db, "career") == []
    response = client.post("/api/memories", json={"search_chunk_id": chunk_id})
    assert response.status_code == 409
    assert "invalid source attribution" in response.json()["detail"]


def test_unchanged_transcript_is_skipped_and_changed_transcript_reindexed(fake_embedder):
    with SessionLocal() as db:
        recording = create_recording(db, segments=[(1, 4, "My internship interview is tomorrow.")])
        first = search.index_recording(recording.id, db)
        first_hash = db.get(SearchIndexState, recording.id).transcript_hash
        calls_after_first = fake_embedder.calls
        second = search.index_recording(recording.id, db)
        assert second.skipped is True
        assert fake_embedder.calls == calls_after_first

        segment = recording.segments[0]
        segment.text = "I bought a new laptop computer."
        recording.transcript_text = segment.text
        db.commit()
        changed = search.index_recording(recording.id, db)
        assert changed.skipped is False
        assert db.get(SearchIndexState, recording.id).transcript_hash != first_hash
        assert search.search(db, "laptop")[0].recording.id == recording.id


def test_rebuild_reconstructs_all_completed_transcripts(fake_embedder):
    with SessionLocal() as db:
        first = create_recording(db, "First", [(0, 2, "career interview")])
        second = create_recording(db, "Second", [(2, 4, "laptop hardware")])
        search.index_recording(first.id, db)
        search.index_recording(second.id, db)
        search.remove_recording_chunks(first.id, db)
        search.remove_recording_chunks(second.id, db)

        rebuilt = search.rebuild_index(db)
        assert rebuilt.indexed_recordings == 2
        assert rebuilt.failed_recordings == 0
        assert rebuilt.indexed_chunks == 2
        assert db.scalar(select(func.count(SearchChunk.id))) == 2


def test_concurrent_index_mutations_are_serialized(monkeypatch):
    with SessionLocal() as db:
        recording = create_recording(db, "Concurrent", [(0, 3, "career interview")])
        recording_id = recording.id

    first_encode_started = threading.Event()
    release_first_encode = threading.Event()
    call_lock = threading.Lock()
    calls = 0

    class BlockingEmbedder:
        def encode(self, texts, normalize_embeddings=True):
            nonlocal calls
            with call_lock:
                calls += 1
                current = calls
            if current == 1:
                first_encode_started.set()
                assert release_first_encode.wait(timeout=5)
            return [[1.0, 0.0] for _text in texts]

    monkeypatch.setattr(search, "get_embedder", lambda: BlockingEmbedder())
    errors = []

    def build_index():
        try:
            with SessionLocal() as thread_db:
                search.index_recording(recording_id, thread_db, force=True)
        except Exception as exc:
            errors.append(exc)

    first = threading.Thread(target=build_index)
    second = threading.Thread(target=build_index)
    first.start()
    assert first_encode_started.wait(timeout=5)
    second.start()
    release_first_encode.set()
    first.join(timeout=10); second.join(timeout=10)
    assert errors == []
    with SessionLocal() as db:
        state = db.get(SearchIndexState, recording_id)
        assert state.status == "ready"
        assert db.scalar(select(func.count(SearchChunk.id)).where(SearchChunk.recording_id == recording_id)) == state.chunk_count


def test_rebuild_api_requires_explicit_json_confirmation(client, fake_embedder):
    with SessionLocal() as db:
        create_recording(db, "Rebuild", [(0, 2, "career interview")])
    assert client.post("/api/search/rebuild").status_code == 422
    response = client.post("/api/search/rebuild", json={"confirm": True})
    assert response.status_code == 200
    assert response.json()["indexed_recordings"] == 1


def test_saved_only_filters_to_chunks_with_saved_source_segments(fake_embedder):
    with SessionLocal() as db:
        career = create_recording(db, "Career", [(0, 3, "career interview")])
        laptop = create_recording(db, "Laptop", [(4, 7, "laptop hardware")])
        search.index_recording(career.id, db)
        search.index_recording(laptop.id, db)
        segment = career.segments[0]
        db.add(SavedMemory(
            id=str(uuid.uuid4()), recording_id=career.id, segment_id=segment.id,
            title=None, note=None, source_text=segment.text,
            source_start_seconds=segment.start_seconds, source_end_seconds=segment.end_seconds,
        ))
        db.commit()
        matches = search.search(db, "career laptop", saved_only=True)
        assert [match.recording.id for match in matches] == [career.id]


def test_search_api_validation_response_and_unavailable_status(client, fake_embedder, monkeypatch):
    with SessionLocal() as db:
        recording = create_recording(db, "Career API", [(5, 9, "My internship interview went well.")])
        search.index_recording(recording.id, db)
        recording_id = recording.id

    assert client.post("/api/search", json={"query": "   "}).status_code == 422
    response = client.post("/api/search", json={"query": "internship interview"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["score_explanation"].endswith("correctness.")
    assert payload["results"][0]["recording_id"] == recording_id
    assert payload["results"][0]["segment_id"]
    assert payload["results"][0]["segment_ids"]
    assert payload["results"][0]["start_seconds"] == 5
    assert payload["results"][0]["end_seconds"] == 9

    monkeypatch.setattr(search, "get_embedder", lambda: (_ for _ in ()).throw(search.SearchUnavailable("Cache the model first")))
    unavailable = client.post("/api/search", json={"query": "internship"})
    assert unavailable.status_code == 503
    assert "Cache the model first" in unavailable.json()["detail"]


def test_index_status_does_not_load_model(client, monkeypatch):
    monkeypatch.setattr(
        search,
        "embedding_runtime_status",
        lambda: search.EmbeddingRuntimeStatus(True, "not_loaded", False, None),
    )
    response = client.get("/api/search/status")
    assert response.status_code == 200
    assert response.json()["model_status"] == "not_loaded"
    assert response.json()["model_loaded"] is False


def test_saved_memory_create_edit_clear_and_delete_from_search_result(client, fake_embedder):
    with SessionLocal() as db:
        recording = create_recording(db, "Source", [(12, 18, "My career interview notes.")])
        search.index_recording(recording.id, db)
        recording_id = recording.id
    result = client.post("/api/search", json={"query": "career interview"}).json()["results"][0]

    created = client.post("/api/memories", json={"search_chunk_id": result["chunk_id"], "title": "Career", "note": "Follow up"})
    assert created.status_code == 201
    memory = created.json()
    assert memory["recording_id"] == recording_id
    assert memory["source_text"] == result["text"]
    assert memory["source_start_seconds"] == 12
    assert memory["source_end_seconds"] == 18

    updated = client.patch(f"/api/memories/{memory['id']}", json={"title": "Interview", "note": "Send email"})
    assert updated.status_code == 200
    assert updated.json()["title"] == "Interview"
    assert updated.json()["source_text"] == memory["source_text"]
    cleared = client.patch(f"/api/memories/{memory['id']}", json={"note": None})
    assert cleared.json()["note"] is None
    assert client.patch(f"/api/memories/{memory['id']}", json={}).status_code == 422
    assert client.delete(f"/api/memories/{memory['id']}").status_code == 204
    assert client.get("/api/memories").json() == []


def test_recording_deletion_cascades_search_data_and_saved_memories(client, fake_embedder):
    with SessionLocal() as db:
        recording = create_recording(db, "Delete me", [(0, 4, "career interview")])
        search.index_recording(recording.id, db)
        segment = recording.segments[0]
        db.add(SavedMemory(
            id=str(uuid.uuid4()), recording_id=recording.id, segment_id=segment.id,
            title="Saved", note=None, source_text=segment.text,
            source_start_seconds=segment.start_seconds, source_end_seconds=segment.end_seconds,
        ))
        db.commit()
        recording_id = recording.id

    assert client.delete(f"/api/recordings/{recording_id}").status_code == 204
    with SessionLocal() as db:
        assert db.get(Recording, recording_id) is None
        assert db.scalar(select(func.count(SearchChunk.id)).where(SearchChunk.recording_id == recording_id)) == 0
        assert db.get(SearchIndexState, recording_id) is None
        assert db.scalar(select(func.count(SavedMemory.id)).where(SavedMemory.recording_id == recording_id)) == 0


def test_phase_one_sqlite_database_migrates_without_losing_recordings(tmp_path):
    database_path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.executescript("""
        CREATE TABLE recordings (
            id VARCHAR(36) PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            original_filename VARCHAR(255) NOT NULL,
            relative_audio_path VARCHAR(255) NOT NULL UNIQUE,
            mime_type VARCHAR(100) NOT NULL,
            file_size INTEGER NOT NULL,
            duration_seconds INTEGER,
            created_at DATETIME NOT NULL,
            transcription_status VARCHAR(30) NOT NULL,
            transcript_text TEXT,
            summary TEXT,
            error_message TEXT
        );
        INSERT INTO recordings VALUES ('legacy', 'Legacy', 'old.wav', 'old.wav', 'audio/wav', 10, NULL, CURRENT_TIMESTAMP, 'not_started', NULL, NULL, NULL);
    """)
    connection.commit()
    connection.close()
    legacy_engine = create_engine(f"sqlite:///{database_path}")
    try:
        migrate_schema(legacy_engine)
        with legacy_engine.connect() as migrated:
            columns = {row[1] for row in migrated.exec_driver_sql("PRAGMA table_info(recordings)")}
            tables = {row[0] for row in migrated.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
            assert migrated.exec_driver_sql("SELECT title FROM recordings WHERE id='legacy'").scalar_one() == "Legacy"
        assert {"transcription_progress", "extraction_status", "extracted_memories"} <= columns
        assert {"search_chunks", "search_index_state", "saved_memories", "schema_migrations"} <= tables
    finally:
        legacy_engine.dispose()


def test_partial_phase_three_derived_tables_are_rebuilt(tmp_path):
    database_path = tmp_path / "partial-phase-three.sqlite3"
    partial_engine = create_engine(f"sqlite:///{database_path}")
    try:
        migrate_schema(partial_engine)
        with partial_engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE search_chunks")
            connection.exec_driver_sql("DROP TABLE search_index_state")
            connection.exec_driver_sql("""
                CREATE TABLE search_chunks (
                    id VARCHAR(36) PRIMARY KEY, recording_id VARCHAR(36), position INTEGER,
                    primary_segment_id VARCHAR(36), segment_ids TEXT, start_seconds FLOAT,
                    end_seconds FLOAT, text TEXT, embedding BLOB, embedding_model VARCHAR(255),
                    embedding_revision VARCHAR(255), embedding_dimension INTEGER,
                    transcript_hash VARCHAR(64), created_at DATETIME
                )
            """)
            connection.exec_driver_sql("INSERT INTO search_chunks VALUES ('one', 'recording', 0, 'segment', '[]', 0, 1, 'one', X'00000000', 'model', NULL, 1, 'hash', CURRENT_TIMESTAMP)")
            connection.exec_driver_sql("INSERT INTO search_chunks VALUES ('two', 'recording', 0, 'segment', '[]', 0, 1, 'two', X'00000000', 'model', NULL, 1, 'hash', CURRENT_TIMESTAMP)")
            connection.exec_driver_sql("CREATE TABLE search_index_state (recording_id VARCHAR(36) PRIMARY KEY, transcript_hash VARCHAR(64), embedding_model VARCHAR(255), status VARCHAR(20))")
        migrate_schema(partial_engine)
        with partial_engine.connect() as connection:
            chunk_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(search_chunks)")}
            state_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(search_index_state)")}
            chunk_count = connection.exec_driver_sql("SELECT COUNT(*) FROM search_chunks").scalar_one()
        assert {"primary_segment_id", "segment_ids", "embedding_revision", "embedding_dimension"} <= chunk_columns
        assert {"embedding_revision", "chunker_version", "chunk_count", "updated_at"} <= state_columns
        assert chunk_count == 0
    finally:
        partial_engine.dispose()


def test_partial_saved_memory_snapshots_are_backfilled_idempotently(tmp_path):
    database_path = tmp_path / "partial-memories.sqlite3"
    partial_engine = create_engine(f"sqlite:///{database_path}")
    try:
        migrate_schema(partial_engine)
        with partial_engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO recordings (id,title,original_filename,relative_audio_path,mime_type,file_size,created_at,transcription_status,transcription_progress,extraction_status) VALUES ('recording','Legacy','legacy.wav','legacy.wav','audio/wav',10,CURRENT_TIMESTAMP,'complete',100,'unavailable')")
            connection.exec_driver_sql("INSERT INTO transcript_segments (id,recording_id,position,start_seconds,end_seconds,text) VALUES ('segment','recording',0,4.0,8.0,'Original evidence')")
            connection.exec_driver_sql("DROP TABLE saved_memories")
            connection.exec_driver_sql("CREATE TABLE saved_memories (id VARCHAR(36) PRIMARY KEY, recording_id VARCHAR(36), segment_id VARCHAR(36) UNIQUE, title VARCHAR(255), note TEXT, source_text TEXT, source_start_seconds FLOAT, source_end_seconds FLOAT, created_at DATETIME, updated_at DATETIME)")
            connection.exec_driver_sql("INSERT INTO saved_memories (id,recording_id,segment_id,title,created_at) VALUES ('memory','recording','segment','Legacy memory',CURRENT_TIMESTAMP)")
        migrate_schema(partial_engine)
        migrate_schema(partial_engine)
        with partial_engine.connect() as connection:
            row = connection.exec_driver_sql("SELECT source_text,source_start_seconds,source_end_seconds,updated_at FROM saved_memories WHERE id='memory'").one()
        assert row[0] == "Original evidence"
        assert row[1:3] == (4.0, 8.0)
        assert row[3] is not None
    finally:
        partial_engine.dispose()
