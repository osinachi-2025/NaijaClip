from __future__ import annotations

import sqlalchemy as sa

import database


def test_initialize_database_adds_missing_job_columns(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("ADMIN_EMAIL", "schema-admin@example.test")
    monkeypatch.setenv("ADMIN_PASSWORD", "SchemaAdminPassword123!")
    monkeypatch.setenv("ADMIN_NAME", "Schema Admin")
    monkeypatch.setattr(database, "engine", database.create_engine(database.DATABASE_URL, pool_pre_ping=True, pool_recycle=1800))
    monkeypatch.setattr(database, "SessionLocal", database.sessionmaker(bind=database.engine, autoflush=False, autocommit=False, expire_on_commit=False))

    import models

    with database.engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(320) NOT NULL, password_hash VARCHAR(255) NOT NULL, first_name VARCHAR(100), last_name VARCHAR(100), role VARCHAR(50) NOT NULL, status VARCHAR(50) NOT NULL, email_verified BOOLEAN NOT NULL DEFAULT 0, email_verified_at DATETIME, avatar_url VARCHAR(2048), created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, last_login_at DATETIME, deleted_at DATETIME)"))
        conn.execute(sa.text("CREATE TABLE videos (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL, title VARCHAR(500) NOT NULL, original_filename VARCHAR(500) NOT NULL, status VARCHAR(50) NOT NULL, visibility VARCHAR(50) NOT NULL, source_url VARCHAR(2048), source_storage_key VARCHAR(1024), source_mime_type VARCHAR(100), file_size_bytes INTEGER, duration_seconds NUMERIC, width INTEGER, height INTEGER, frame_rate NUMERIC, video_codec VARCHAR(50), audio_codec VARCHAR(50), clip_count INTEGER NOT NULL DEFAULT 0, processing_error TEXT, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, completed_at DATETIME, deleted_at DATETIME)"))
        conn.execute(sa.text("CREATE TABLE clips (id VARCHAR(36) PRIMARY KEY, output_storage_key VARCHAR(1024))"))
        conn.execute(sa.text("CREATE TABLE processing_jobs (id VARCHAR(36) PRIMARY KEY, video_id VARCHAR(36) NOT NULL, job_type VARCHAR(50) NOT NULL, status VARCHAR(50) NOT NULL, queue_id VARCHAR(255), worker_id VARCHAR(255), progress INTEGER NOT NULL DEFAULT 0, current_stage VARCHAR(100), attempt_count INTEGER NOT NULL DEFAULT 0, error_message TEXT, failed_at DATETIME, max_attempts INTEGER NOT NULL DEFAULT 3, claimed_at DATETIME, started_at DATETIME, completed_at DATETIME, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"))
        conn.execute(sa.text("INSERT INTO users (id, email, password_hash, first_name, last_name, role, status, email_verified, email_verified_at, avatar_url, created_at, updated_at, last_login_at, deleted_at) VALUES ('user-1', 'demo@example.com', 'hash', 'Demo', 'User', 'user', 'active', 1, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, NULL)"))
        conn.execute(sa.text("INSERT INTO videos (id, user_id, title, original_filename, status, visibility, source_url, source_storage_key, source_mime_type, file_size_bytes, duration_seconds, width, height, frame_rate, video_codec, audio_codec, clip_count, processing_error, created_at, updated_at, completed_at, deleted_at) VALUES ('video-1', 'user-1', 'Legacy video', 'legacy.mp4', 'queued', 'private', NULL, 'sources/user-1/legacy.mp4', 'video/mp4', 1234, 30.0, 1280, 720, 30.0, 'h264', 'aac', 0, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, NULL)"))
        conn.execute(sa.text("INSERT INTO processing_jobs (id, video_id, job_type, status, progress, current_stage, attempt_count, error_message, failed_at, max_attempts, claimed_at, started_at, completed_at, created_at, updated_at) VALUES ('job-1', 'video-1', 'export', 'queued', 0, 'queued', 0, NULL, NULL, 3, NULL, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))

    database.initialize_database()

    with database.engine.begin() as conn:
        user_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(users)").fetchall()}
        for column_name in {
            "is_active",
            "auth_provider",
            "google_id",
            "plan",
            "subscription_status",
            "subscription_expires_at",
            "videos_used",
        }:
            assert column_name in user_columns

        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(processing_jobs)").fetchall()}
        assert "error_code" in columns
        assert "safe_error_message" in columns
        assert "internal_error_details" in columns
        assert "retryable" in columns
        assert "last_heartbeat_at" in columns
        assert "cancelled_at" in columns

        video_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(videos)").fetchall()}
        assert "audio_quality_status" in video_columns
        assert "media_metadata" in video_columns

        clip_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(clips)").fetchall()}
        assert "editor_source_storage_key" in clip_columns
        assert "thumbnail_storage_key" in clip_columns
        assert "is_featured" in clip_columns
        assert "featured_order" in clip_columns
        assert "featured_at" in clip_columns


def test_user_plan_legacy_lowercase_value_loads(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-plan.db"
    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", database.create_engine(database.DATABASE_URL, pool_pre_ping=True, pool_recycle=1800))
    monkeypatch.setattr(database, "SessionLocal", database.sessionmaker(bind=database.engine, autoflush=False, autocommit=False, expire_on_commit=False))

    import models

    with database.engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(320) NOT NULL, password_hash VARCHAR(255) NOT NULL, first_name VARCHAR(100), last_name VARCHAR(100), role VARCHAR(50) NOT NULL, status VARCHAR(50) NOT NULL, email_verified BOOLEAN NOT NULL DEFAULT 0, email_verified_at DATETIME, is_active BOOLEAN NOT NULL DEFAULT 1, auth_provider VARCHAR(50) NOT NULL DEFAULT 'local', google_id VARCHAR(255), plan VARCHAR(20) NOT NULL DEFAULT 'free', subscription_status VARCHAR(50) NOT NULL DEFAULT 'inactive', subscription_expires_at DATETIME, videos_used INTEGER NOT NULL DEFAULT 0, avatar_url VARCHAR(2048), created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, last_login_at DATETIME, deleted_at DATETIME)"))
        conn.execute(sa.text("INSERT INTO users (id, email, password_hash, first_name, last_name, role, status, email_verified, is_active, auth_provider, plan, subscription_status, created_at, updated_at) VALUES ('user-1', 'demo@example.com', 'hash', 'Demo', 'User', 'user', 'active', 1, 1, 'local', 'free', 'inactive', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))

    with database.SessionLocal() as db:
        user = db.get(models.User, 'user-1')
        assert user is not None
        assert user.plan == models.PlanCode.FREE
