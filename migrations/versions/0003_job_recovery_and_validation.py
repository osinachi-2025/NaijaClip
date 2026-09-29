"""add job recovery and upload validation metadata

Revision ID: 0003_job_recovery_and_validation
Revises: 0002_processing_job_lifecycle
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_job_recovery_and_validation"
down_revision = "0002_processing_job_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    processing_columns = {column["name"] for column in sa.inspect(bind).get_columns("processing_jobs")}
    video_columns = {column["name"] for column in sa.inspect(bind).get_columns("videos")}

    if "error_code" not in processing_columns:
        op.add_column("processing_jobs", sa.Column("error_code", sa.String(length=50), nullable=True))
    if "safe_error_message" not in processing_columns:
        op.add_column("processing_jobs", sa.Column("safe_error_message", sa.Text(), nullable=True))
    if "internal_error_details" not in processing_columns:
        op.add_column("processing_jobs", sa.Column("internal_error_details", sa.Text(), nullable=True))
    if "retryable" not in processing_columns:
        op.add_column("processing_jobs", sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()))
        op.alter_column("processing_jobs", "retryable", server_default=None)
    if "last_heartbeat_at" not in processing_columns:
        op.add_column("processing_jobs", sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    if "cancelled_at" not in processing_columns:
        op.add_column("processing_jobs", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))

    if "audio_quality_status" not in video_columns:
        op.add_column("videos", sa.Column("audio_quality_status", sa.String(length=50), nullable=True))
    if "media_metadata" not in video_columns:
        op.add_column("videos", sa.Column("media_metadata", sa.Text(), nullable=True))


def downgrade():
    bind = op.get_bind()
    processing_columns = {column["name"] for column in sa.inspect(bind).get_columns("processing_jobs")}
    video_columns = {column["name"] for column in sa.inspect(bind).get_columns("videos")}

    for column_name in ["cancelled_at", "last_heartbeat_at", "retryable", "internal_error_details", "safe_error_message", "error_code"]:
        if column_name in processing_columns:
            op.drop_column("processing_jobs", column_name)

    for column_name in ["media_metadata", "audio_quality_status"]:
        if column_name in video_columns:
            op.drop_column("videos", column_name)
