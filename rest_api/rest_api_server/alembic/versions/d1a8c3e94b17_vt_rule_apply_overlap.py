"""vt rule apply overlap and changed-only reapply

Revision ID: d1a8c3e94b17
Revises: b8c4d1e70a22
Create Date: 2026-08-25 16:20:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'd1a8c3e94b17'
down_revision = 'b8c4d1e70a22'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'virtual_tag_rule',
        sa.Column('updated_at', sa.Integer(), nullable=False, server_default='0'))
    op.add_column(
        'virtual_tag_rule',
        sa.Column('last_applied_at', sa.Integer(), nullable=False,
                  server_default='0'))
    op.add_column(
        'virtual_tag_rule',
        sa.Column('overlap_rule_ids', sa.Text(), nullable=True))
    op.execute(
        'UPDATE virtual_tag_rule SET updated_at = created_at '
        'WHERE updated_at = 0')
    op.execute(
        'UPDATE virtual_tag_rule SET last_applied_at = updated_at '
        'WHERE last_applied_at = 0')
    op.alter_column(
        'virtual_tag_rule', 'updated_at', server_default=None)
    op.alter_column(
        'virtual_tag_rule', 'last_applied_at', server_default=None)


def downgrade():
    op.drop_column('virtual_tag_rule', 'overlap_rule_ids')
    op.drop_column('virtual_tag_rule', 'last_applied_at')
    op.drop_column('virtual_tag_rule', 'updated_at')
