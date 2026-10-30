# Database migrations

LastWhisper currently targets SQLite and runs the idempotent migration bridge in `app/database.py` at API startup. Applied phase numbers are recorded in `schema_migrations`.

- Phase 2 adds recording transcription and enrichment columns without replacing the Phase 1 database.
- Phase 3 creates the search-index, index-state, and saved-memory tables. It also upgrades tables created by an early Phase 3 development build.
- Search chunks and embeddings are derived data, so the Phase 3 bridge may clear incompatible search rows; it never clears recordings, transcript segments, audio, or valid saved-memory source data.
- SQLite foreign keys are enabled on every application connection so recording and segment deletion cascades are enforced.

`Base.metadata.create_all()` still initializes new databases, but the explicit bridge handles existing tables because `create_all()` does not add columns. Introduce Alembic before a future migration that needs destructive or data-transforming schema changes beyond this small SQLite bridge.
