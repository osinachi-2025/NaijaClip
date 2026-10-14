"""persist Groq transcript chunk results

Revision ID: 0010_groq_chunk_checkpoints
Revises: 0009_email_verification_resend_throttles
"""
from alembic import op
import sqlalchemy as sa


revision = "0010_groq_chunk_checkpoints"
down_revision = "0009_email_verification_resend_throttles"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("processing_jobs")}
    if "groq_selection_state" not in columns:
        op.add_column("processing_jobs", sa.Column("groq_selection_state", sa.Text(), nullable=True))


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("processing_jobs")}
    if "groq_selection_state" in columns:
        op.drop_column("processing_jobs", "groq_selection_state")
