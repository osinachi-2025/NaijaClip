"""add durable processing job dispatch leases

Revision ID: 0008_job_dispatch_leases
Revises: 0007_featured_homepage_clips
"""
from alembic import op
import sqlalchemy as sa


revision = "0008_job_dispatch_leases"
down_revision = "0007_featured_homepage_clips"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("processing_jobs")}
    additions = {
        "dispatch_token": sa.Column("dispatch_token", sa.String(length=36), nullable=True),
        "dispatch_lease_until": sa.Column("dispatch_lease_until", sa.DateTime(timezone=True), nullable=True),
        "dispatch_retry_at": sa.Column("dispatch_retry_at", sa.DateTime(timezone=True), nullable=True),
        "dispatch_attempts": sa.Column("dispatch_attempts", sa.Integer(), nullable=False, server_default="0"),
        "dispatch_published_at": sa.Column("dispatch_published_at", sa.DateTime(timezone=True), nullable=True),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("processing_jobs", column)


def downgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("processing_jobs")}
    for name in ("dispatch_published_at", "dispatch_attempts", "dispatch_retry_at", "dispatch_lease_until", "dispatch_token"):
        if name in columns:
            op.drop_column("processing_jobs", name)