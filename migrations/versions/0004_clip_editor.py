"""persist browser clip edits and export jobs

Revision ID: 0004_clip_editor
Revises: 0003_job_recovery_and_validation
"""
from alembic import op
import sqlalchemy as sa

import models

revision = "0004_clip_editor"
down_revision = "0003_job_recovery_and_validation"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    models.ClipEdit.__table__.create(bind, checkfirst=True)
    columns = {column["name"] for column in sa.inspect(bind).get_columns("clip_exports")}
    if "edit_configuration" not in columns:
        op.add_column("clip_exports", sa.Column("edit_configuration", sa.JSON(), nullable=True))
    if "processing_job_id" not in columns:
        op.add_column("clip_exports", sa.Column("processing_job_id", sa.String(length=36), nullable=True))

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("clip_exports")}
    if "ix_clip_exports_processing_job_id" not in indexes:
        op.create_index("ix_clip_exports_processing_job_id", "clip_exports", ["processing_job_id"], unique=True)


def downgrade():
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("clip_exports")}
    if "ix_clip_exports_processing_job_id" in indexes:
        op.drop_index("ix_clip_exports_processing_job_id", table_name="clip_exports")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("clip_exports")}
    if "processing_job_id" in columns:
        op.drop_column("clip_exports", "processing_job_id")
    if "edit_configuration" in columns:
        op.drop_column("clip_exports", "edit_configuration")
    models.ClipEdit.__table__.drop(bind, checkfirst=True)