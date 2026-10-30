from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, hide_parameters=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _column_names(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}


def _add_columns(conn, table: str, additions: dict[str, str]) -> bool:
    existing = _column_names(conn, table)
    changed = False
    for name, definition in additions.items():
        if name not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
            changed = True
    return changed


def _has_unique_index(conn, table: str, columns: tuple[str, ...]) -> bool:
    for row in conn.execute(text(f"PRAGMA index_list({table})")):
        if not row[2]:
            continue
        index_name = str(row[1]).replace('"', '""')
        indexed_columns = tuple(
            index_row[2]
            for index_row in conn.execute(text(f'PRAGMA index_info("{index_name}")'))
        )
        if indexed_columns == columns:
            return True
    return False


def migrate_schema(bind: Engine | None = None) -> None:
    """Apply the additive SQLite migrations used by released app phases.

    Search embeddings are rebuildable, so an early Phase 3 search table can be
    upgraded safely by clearing only its derived rows. Recordings, transcripts,
    audio, and saved-memory source data are retained.
    """
    target = bind or engine
    Base.metadata.create_all(bind=target)
    if target.dialect.name != "sqlite":
        return
    with target.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"))
        _add_columns(conn, "recordings", {
            "transcription_language": "VARCHAR(32)",
            "selected_language": "VARCHAR(32)",
            "transcription_progress": "INTEGER NOT NULL DEFAULT 0",
            "extraction_status": "VARCHAR(30) NOT NULL DEFAULT 'unavailable'",
            "extracted_memories": "TEXT",
        })
        required_search_columns = {
            "id", "recording_id", "position", "primary_segment_id", "segment_ids",
            "start_seconds", "end_seconds", "text", "embedding", "embedding_model",
            "embedding_revision", "embedding_dimension", "transcript_hash", "created_at",
        }
        required_state_columns = {
            "recording_id", "transcript_hash", "embedding_model", "embedding_revision",
            "embedding_dimension", "chunk_characters", "chunk_overlap_segments",
            "chunker_version", "chunk_count", "status", "error_message", "indexed_at",
            "updated_at",
        }
        search_changed = (
            not required_search_columns.issubset(_column_names(conn, "search_chunks"))
            or not _has_unique_index(conn, "search_chunks", ("recording_id", "position"))
        )
        state_changed = not required_state_columns.issubset(_column_names(conn, "search_index_state"))
        if search_changed:
            conn.execute(text("DROP TABLE search_chunks"))
            Base.metadata.tables["search_chunks"].create(bind=conn)
        if state_changed:
            conn.execute(text("DROP TABLE search_index_state"))
            Base.metadata.tables["search_index_state"].create(bind=conn)
        _add_columns(conn, "saved_memories", {
            "source_text": "TEXT",
            "source_start_seconds": "FLOAT",
            "source_end_seconds": "FLOAT",
            "updated_at": "DATETIME",
        })
        if search_changed or state_changed:
            conn.execute(text("DELETE FROM search_chunks"))
            conn.execute(text("DELETE FROM search_index_state"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_search_chunks_recording_position ON search_chunks(recording_id, position)"))
        conn.execute(text("DELETE FROM saved_memories WHERE NOT EXISTS (SELECT 1 FROM transcript_segments s WHERE s.id = saved_memories.segment_id AND s.recording_id = saved_memories.recording_id)"))
        conn.execute(text("UPDATE saved_memories SET source_text = (SELECT text FROM transcript_segments WHERE id = saved_memories.segment_id), source_start_seconds = (SELECT start_seconds FROM transcript_segments WHERE id = saved_memories.segment_id), source_end_seconds = (SELECT end_seconds FROM transcript_segments WHERE id = saved_memories.segment_id), updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) WHERE source_text IS NULL OR source_start_seconds IS NULL OR source_end_seconds IS NULL OR updated_at IS NULL"))
        conn.execute(text("INSERT OR IGNORE INTO schema_migrations(version, name) VALUES (2, 'phase_2_transcription')"))
        conn.execute(text("INSERT OR IGNORE INTO schema_migrations(version, name) VALUES (3, 'phase_3_semantic_search')"))
