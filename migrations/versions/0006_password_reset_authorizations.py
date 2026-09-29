"""add one-time password reset authorization records

Revision ID: 0006_password_reset_authorizations
Revises: 0005_clip_editor_source
"""
from alembic import op
import sqlalchemy as sa


revision = "0006_password_reset_authorizations"
down_revision = "0005_clip_editor_source"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    table_name = "password_reset_authorizations"
    if not sa.inspect(bind).has_table(table_name):
        op.create_table(
            table_name,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes(table_name)}
    if "ix_password_reset_authorizations_user_id" not in indexes:
        op.create_index("ix_password_reset_authorizations_user_id", table_name, ["user_id"])


def downgrade():
    bind = op.get_bind()
    table_name = "password_reset_authorizations"
    if sa.inspect(bind).has_table(table_name):
        indexes = {index["name"] for index in sa.inspect(bind).get_indexes(table_name)}
        if "ix_password_reset_authorizations_user_id" in indexes:
            op.drop_index("ix_password_reset_authorizations_user_id", table_name=table_name)
        op.drop_table(table_name)