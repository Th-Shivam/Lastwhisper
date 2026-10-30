"""Opt-in end-to-end verification using real cached local AI models."""
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import app
from app.models import SavedMemory, SearchChunk, SearchIndexState


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_REAL_MODEL_TESTS") != "1",
        reason="Set RUN_REAL_MODEL_TESTS=1 with cached model weights and real audio fixtures.",
    ),
]


def _audio_path(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        pytest.skip(f"{name} is required for the real-model test")
    path = Path(value)
    if not path.is_file():
        pytest.skip(f"{name} does not point to a readable audio file")
    return path


def _import_audio(client: TestClient, path: Path) -> dict:
    with path.open("rb") as audio:
        response = client.post(
            "/api/recordings/import",
            files={"file": (path.name, audio, "audio/wav")},
        )
    assert response.status_code == 201, response.text
    return response.json()


def _transcribe(client: TestClient, recording_id: str) -> dict:
    response = client.post(
        f"/api/recordings/{recording_id}/transcribe",
        json={"language": "en"},
    )
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        detail = client.get(f"/api/recordings/{recording_id}").json()
        if detail["transcription_status"] == "complete":
            assert detail["segments"]
            assert detail["transcription_language"] == "en"
            return detail
        if detail["transcription_status"] == "failed":
            pytest.fail(detail["error_message"])
        time.sleep(0.5)
    pytest.fail("Real transcription did not finish within five minutes")


def _wait_for_index(client: TestClient, recordings: int) -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        status = client.get("/api/search/status").json()
        if status["indexed_recordings"] >= recordings and status["failed_recordings"] == 0:
            return
        time.sleep(0.5)
    pytest.fail("Real semantic indexing did not finish within three minutes")


def _search(client: TestClient, query: str, expected_recording_id: str) -> dict:
    response = client.post("/api/search", json={"query": query, "limit": 5})
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert results
    result = next((item for item in results if item["recording_id"] == expected_recording_id), None)
    assert result is not None
    assert result["segment_id"] in result["segment_ids"]
    assert result["end_seconds"] > result["start_seconds"] >= 0
    return result


def test_real_local_ai_journey_persists_and_cleans_up():
    first_audio = _audio_path("REAL_AUDIO_FILE")
    second_audio = _audio_path("REAL_AUDIO_FILE_2")

    with TestClient(app) as client:
        assert client.get("/api/ready").status_code == 200
        first = _import_audio(client, first_audio)
        second = _import_audio(client, second_audio)
        assert client.get(f"/api/recordings/{first['id']}/audio").content == first_audio.read_bytes()

        first_detail = _transcribe(client, first["id"])
        second_detail = _transcribe(client, second["id"])
        assert first_detail["extraction_status"] == "unavailable"
        assert second_detail["extraction_status"] == "unavailable"
        assert any(segment["start_seconds"] >= 0 for segment in first_detail["segments"])
        assert any(segment["start_seconds"] >= 0 for segment in second_detail["segments"])
        _wait_for_index(client, 2)

        project_result = _search(client, "private local voice memory project", first["id"])
        family_result = _search(client, "flowers in the family garden", second["id"])
        assert "project" in project_result["text"].lower()
        assert any(word in family_result["text"].lower() for word in ("garden", "family", "marigold"))

        first_memory = client.post("/api/memories", json={"search_chunk_id": project_result["chunk_id"]})
        second_memory = client.post("/api/memories", json={"search_chunk_id": family_result["chunk_id"]})
        assert first_memory.status_code == second_memory.status_code == 201
        edited = client.patch(
            f"/api/memories/{second_memory.json()['id']}",
            json={"title": "Family garden", "note": "Call on Sunday"},
        )
        assert edited.status_code == 200
        assert edited.json()["source_text"] == second_memory.json()["source_text"]
        assert _search(client, "project roadmap and privacy", first["id"])

        assert client.delete(f"/api/recordings/{first['id']}").status_code == 204
        with SessionLocal() as db:
            assert db.scalar(select(func.count(SearchChunk.id)).where(SearchChunk.recording_id == first["id"])) == 0
            assert db.get(SearchIndexState, first["id"]) is None
            assert db.scalar(select(func.count(SavedMemory.id)).where(SavedMemory.recording_id == first["id"])) == 0

    # A fresh lifespan uses the same SQLite file and local storage.
    with TestClient(app) as restarted:
        recordings = restarted.get("/api/recordings").json()
        memories = restarted.get("/api/memories").json()
        assert [item["id"] for item in recordings] == [second["id"]]
        assert memories[0]["title"] == "Family garden"
        persisted = restarted.get(f"/api/recordings/{second['id']}").json()
        assert persisted["segments"] == second_detail["segments"]
        assert restarted.delete(f"/api/recordings/{second['id']}").status_code == 204
