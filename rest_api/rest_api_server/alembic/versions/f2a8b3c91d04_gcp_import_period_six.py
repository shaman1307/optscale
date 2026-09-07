"""Set import_period=6 for existing GCP cloud accounts

Revision ID: f2a8b3c91d04
Revises: e4b7d2c81a09
Create Date: 2026-09-01 10:35:00.000000

"""
from alembic import op

revision = 'f2a8b3c91d04'
down_revision = 'e4b7d2c81a09'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE cloudaccount SET import_period = 6 "
        "WHERE deleted_at = 0 AND type = 'gcp_cnr' AND import_period != 6")


def downgrade():
    pass
