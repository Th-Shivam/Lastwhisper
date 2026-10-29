# Database migrations

Phase 1 uses `Base.metadata.create_all()` at application startup because the initial schema has no prior deployment to migrate.

Before a schema change is released, introduce Alembic in this directory and commit one reviewable revision per change. Do not rely on `create_all()` to alter existing SQLite tables: it only creates missing tables.
