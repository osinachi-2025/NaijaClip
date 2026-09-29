"""create the NaijaClip domain schema

Revision ID: 0001_initial
"""
from alembic import op
import models

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    models.Base.metadata.create_all(bind=op.get_bind())


def downgrade():
    models.Base.metadata.drop_all(bind=op.get_bind())
