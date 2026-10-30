import os
import shutil
import tempfile
from pathlib import Path

import pytest


TEST_ROOT = Path(tempfile.mkdtemp(prefix="lastwhisper-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_ROOT / 'test.sqlite3'}"
os.environ["STORAGE_DIR"] = str(TEST_ROOT / "audio")
os.environ["EMBEDDING_LOCAL_FILES_ONLY"] = "true"

from app import search, transcription  # noqa: E402
from app.database import Base, SessionLocal, engine, migrate_schema  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_database():
    migrate_schema()
    Path(os.environ["STORAGE_DIR"]).mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(table.delete())
        db.commit()
    search.reset_embedding_cache()
    with transcription.active_lock:
        transcription.active_jobs.clear()
    transcription.shutdown_requested.clear()
    yield
    with SessionLocal() as db:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(table.delete())
        db.commit()
    search.reset_embedding_cache()


def pytest_sessionfinish(session, exitstatus):
    del session, exitstatus
    engine.dispose()
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
