# app/database.py

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()


# ============================================================
# DATABASE URL
# ============================================================

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Add DATABASE_URL to your .env file."
    )

if DATABASE_URL.startswith("sqlite:///") and not DATABASE_URL.startswith("sqlite:////"):
    sqlite_path = Path(DATABASE_URL.removeprefix("sqlite:///"))
    if not sqlite_path.is_absolute():
        sqlite_path = Path(__file__).resolve().parent / sqlite_path
    DATABASE_URL = f"sqlite:///{sqlite_path}"


# ============================================================
# SQLALCHEMY BASE
# ============================================================

class Base(DeclarativeBase):
    pass


def _ensure_legacy_schema_compatibility() -> None:
    """Backfill newly added columns on existing SQLite databases.

    SQLAlchemy's create_all() creates missing tables but does not add new columns to
    tables that already exist. This compatibility hook keeps older local SQLite
    databases working with the newer job recovery fields used by the worker.
    """
    if not DATABASE_URL.startswith("sqlite"):
        return

    from sqlalchemy import inspect, text

    with engine.begin() as conn:
        table_names = set(inspect(conn).get_table_names())

        if "users" in table_names:
            user_columns = {column["name"] for column in inspect(conn).get_columns("users")}
            for column_name, definition in {
                "is_active": "BOOLEAN NOT NULL DEFAULT 1",
                "auth_provider": "VARCHAR(50) NOT NULL DEFAULT 'local'",
                "google_id": "VARCHAR(255)",
                "plan": "VARCHAR(4) NOT NULL DEFAULT 'free'",
                "subscription_status": "VARCHAR(50) NOT NULL DEFAULT 'inactive'",
                "subscription_expires_at": "DATETIME",
                "videos_used": "INTEGER NOT NULL DEFAULT 0",
            }.items():
                if column_name not in user_columns:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {definition}"))

        if "processing_jobs" in table_names:
            processing_columns = {column["name"] for column in inspect(conn).get_columns("processing_jobs")}
            for column_name, definition in {
                "error_code": "VARCHAR(50)",
                "safe_error_message": "TEXT",
                "internal_error_details": "TEXT",
                "retryable": "BOOLEAN NOT NULL DEFAULT 0",
                "last_heartbeat_at": "DATETIME",
                "cancelled_at": "DATETIME",
            }.items():
                if column_name not in processing_columns:
                    conn.execute(text(f"ALTER TABLE processing_jobs ADD COLUMN {column_name} {definition}"))

        if "videos" in table_names:
            video_columns = {column["name"] for column in inspect(conn).get_columns("videos")}
            for column_name, definition in {
                "audio_quality_status": "VARCHAR(50)",
                "media_metadata": "TEXT",
            }.items():
                if column_name not in video_columns:
                    conn.execute(text(f"ALTER TABLE videos ADD COLUMN {column_name} {definition}"))

        if "clips" in table_names:
            clip_columns = {column["name"] for column in inspect(conn).get_columns("clips")}
            for column_name, definition in {
                "editor_source_storage_key": "VARCHAR(1024)",
                "thumbnail_storage_key": "VARCHAR(1024)",
                "is_featured": "BOOLEAN NOT NULL DEFAULT 0",
                "featured_order": "INTEGER",
                "featured_at": "DATETIME",
            }.items():
                if column_name not in clip_columns:
                    conn.execute(text(f"ALTER TABLE clips ADD COLUMN {column_name} {definition}"))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_clips_featured_order "
                "ON clips (is_featured, featured_order)"
            ))

        if "clip_exports" in table_names:
            export_columns = {column["name"] for column in inspect(conn).get_columns("clip_exports")}
            for column_name, definition in {
                "edit_configuration": "JSON",
                "processing_job_id": "VARCHAR(36)",
            }.items():
                if column_name not in export_columns:
                    conn.execute(text(f"ALTER TABLE clip_exports ADD COLUMN {column_name} {definition}"))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_clip_exports_processing_job_id "
                "ON clip_exports (processing_job_id)"
            ))


# ============================================================
# ENGINE
# ============================================================

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=1800,
)


# ============================================================
# SESSION FACTORY
# ============================================================

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


# ============================================================
# FASTAPI DATABASE DEPENDENCY
# ============================================================

def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


def initialize_database() -> None:
    import models

    Base.metadata.create_all(bind=engine)
    _ensure_legacy_schema_compatibility()

    from services.auth_service import ensure_initial_admin
    from services.plan_service import ensure_default_plans

    ensure_default_plans()
    ensure_initial_admin()