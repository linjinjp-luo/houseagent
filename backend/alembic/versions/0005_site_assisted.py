"""site assisted import settings and user-added sites

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-27 10:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sites", schema=None) as batch_op:
        batch_op.add_column(sa.Column("assisted_mode", sa.String(length=10), server_default="auto", nullable=False))
        batch_op.add_column(sa.Column("verification_detected_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("verification_probe", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("custom_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sites", schema=None) as batch_op:
        batch_op.drop_column("custom_config")
        batch_op.drop_column("verification_probe")
        batch_op.drop_column("verification_detected_at")
        batch_op.drop_column("assisted_mode")
