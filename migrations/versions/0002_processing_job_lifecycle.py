"""add processing job lifecycle fields

Revision ID: 0002_processing_job_lifecycle
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_processing_job_lifecycle"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("processing_jobs")}
    if "current_stage" not in existing:
        op.add_column("processing_jobs", sa.Column("current_stage", sa.String(length=100), nullable=True))
    if "failed_at" not in existing:
        op.add_column("processing_jobs", sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True))
    if "max_attempts" not in existing:
        op.add_column("processing_jobs", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"))
        op.alter_column("processing_jobs", "max_attempts", server_default=None)
    if "claimed_at" not in existing:
        op.add_column("processing_jobs", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("processing_jobs", "claimed_at")
    op.drop_column("processing_jobs", "max_attempts")
    op.drop_column("processing_jobs", "failed_at")
    op.drop_column("processing_jobs", "current_stage")
