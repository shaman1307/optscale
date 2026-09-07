"""backfill vt rule last_applied_at for pre-tracking rules

Revision ID: e4b7d2c81a09
Revises: d1a8c3e94b17
Create Date: 2026-08-25 17:30:00.000000

"""
from alembic import op

revision = 'e4b7d2c81a09'
down_revision = 'd1a8c3e94b17'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        'UPDATE virtual_tag_rule SET last_applied_at = updated_at '
        'WHERE last_applied_at = 0 AND updated_at > 0')


def downgrade():
    pass
