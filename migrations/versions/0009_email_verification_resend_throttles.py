"""add email verification resend throttles

Revision ID: 0009_email_verification_resend_throttles
Revises: 0008_job_dispatch_leases
"""
from alembic import op
import sqlalchemy as sa


revision = "0009_email_verification_resend_throttles"
down_revision = "0008_job_dispatch_leases"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    table_name = "email_verification_resend_throttles"
    if not sa.inspect(bind).has_table(table_name):
        op.create_table(
            table_name,
            sa.Column("scope_hash", sa.String(length=64), primary_key=True),
            sa.Column("last_requested_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table("email_verification_resend_throttles"):
        op.drop_table("email_verification_resend_throttles")