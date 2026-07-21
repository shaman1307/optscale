"""report_import_details

Revision ID: c8f3a1b92e04
Revises: b7e2c9a14f01
Create Date: 2026-07-21 22:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'c8f3a1b92e04'
down_revision = 'b7e2c9a14f01'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'reportimport',
        sa.Column('details', mysql.MEDIUMTEXT(), nullable=True))


def downgrade():
    op.drop_column('reportimport', 'details')
