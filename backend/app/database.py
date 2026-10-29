from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def migrate_schema() -> None:
    """Small, explicit SQLite migration bridge for Phase 1 databases.

    New deployments use metadata creation; existing installs get additive columns.
    Future non-additive changes should use Alembic revisions.
    """
    Base.metadata.create_all(bind=engine)
    if not settings.database_url.startswith("sqlite"):
        return
    additions = {
        "transcription_language": "VARCHAR(32)", "selected_language": "VARCHAR(32)",
        "transcription_progress": "INTEGER NOT NULL DEFAULT 0", "extraction_status": "VARCHAR(30) NOT NULL DEFAULT 'unavailable'",
        "extracted_memories": "TEXT",
    }
    with engine.begin() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(recordings)"))}
        for name, definition in additions.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE recordings ADD COLUMN {name} {definition}"))

