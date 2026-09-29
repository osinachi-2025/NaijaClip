"""store caption-free reframed source for browser clip editing

Revision ID: 0005_clip_editor_source
Revises: 0004_clip_editor
"""
from alembic import op
import sqlalchemy as sa


revision = "0005_clip_editor_source"
down_revision = "0004_clip_editor"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("clips")}
    if "editor_source_storage_key" not in columns:
        op.add_column("clips", sa.Column("editor_source_storage_key", sa.String(length=1024), nullable=True))


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("clips")}
    if "editor_source_storage_key" in columns:
        op.drop_column("clips", "editor_source_storage_key")