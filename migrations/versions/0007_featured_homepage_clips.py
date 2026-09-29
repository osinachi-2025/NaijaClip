"""add featured homepage clip metadata

Revision ID: 0007_featured_homepage_clips
Revises: 0006_password_reset_authorizations
"""
from alembic import op
import sqlalchemy as sa


revision = "0007_featured_homepage_clips"
down_revision = "0006_password_reset_authorizations"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("clips")}
    additions = {
        "thumbnail_storage_key": sa.Column("thumbnail_storage_key", sa.String(length=1024), nullable=True),
        "is_featured": sa.Column("is_featured", sa.Boolean(), nullable=False, server_default=sa.false()),
        "featured_order": sa.Column("featured_order", sa.Integer(), nullable=True),
        "featured_at": sa.Column("featured_at", sa.DateTime(timezone=True), nullable=True),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("clips", column)

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("clips")}
    if "ix_clips_featured_order" not in indexes:
        op.create_index("ix_clips_featured_order", "clips", ["is_featured", "featured_order"])


def downgrade():
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("clips")}
    if "ix_clips_featured_order" in indexes:
        op.drop_index("ix_clips_featured_order", table_name="clips")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("clips")}
    for name in ("featured_at", "featured_order", "is_featured", "thumbnail_storage_key"):
        if name in columns:
            op.drop_column("clips", name)