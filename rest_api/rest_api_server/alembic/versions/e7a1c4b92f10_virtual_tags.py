"""virtual_tags

Revision ID: e7a1c4b92f10
Revises: d4e8f1a70b22
Create Date: 2026-08-13 21:20:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'e7a1c4b92f10'
down_revision = 'd4e8f1a70b22'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'virtual_tag',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.Integer(), nullable=False),
        sa.Column('deleted_at', sa.Integer(), nullable=False),
        sa.Column('organization_id', mysql.VARCHAR(length=36), nullable=False),
        sa.Column('key', sa.String(length=256), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column(
            'mode', sa.Enum('extract', 'assignment', name='virtualtagmodes'),
            nullable=False),
        sa.Column('source_tag_key', sa.String(length=256), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'key', 'deleted_at', 'organization_id',
            name='uc_virtual_tag_key_del_at_org_id'),
        sa.UniqueConstraint(
            'name', 'deleted_at', 'organization_id',
            name='uc_virtual_tag_name_del_at_org_id'),
    )
    op.create_table(
        'virtual_tag_rule',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.Integer(), nullable=False),
        sa.Column('deleted_at', sa.Integer(), nullable=False),
        sa.Column('organization_id', mysql.VARCHAR(length=36), nullable=False),
        sa.Column('virtual_tag_id', mysql.VARCHAR(length=36), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('creator_id', mysql.VARCHAR(length=36), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id']),
        sa.ForeignKeyConstraint(['virtual_tag_id'], ['virtual_tag.id']),
        sa.ForeignKeyConstraint(['creator_id'], ['employee.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'name', 'deleted_at', 'virtual_tag_id',
            name='uc_virtual_tag_rule_name_del_at_vt_id'),
        sa.UniqueConstraint(
            'priority', 'deleted_at', 'virtual_tag_id',
            name='uc_virtual_tag_rule_priority_del_at_vt_id'),
    )
    op.create_table(
        'virtual_tag_rule_branch',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.Integer(), nullable=False),
        sa.Column('deleted_at', sa.Integer(), nullable=False),
        sa.Column('rule_id', mysql.VARCHAR(length=36), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['rule_id'], ['virtual_tag_rule.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'virtual_tag_rule_condition',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.Integer(), nullable=False),
        sa.Column('deleted_at', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(length=64), nullable=False),
        sa.Column('branch_id', mysql.VARCHAR(length=36), nullable=False),
        sa.Column('meta_info', sa.String(length=256), nullable=True),
        sa.ForeignKeyConstraint(
            ['branch_id'], ['virtual_tag_rule_branch.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'virtual_tag_rule_allocation',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.Integer(), nullable=False),
        sa.Column('deleted_at', sa.Integer(), nullable=False),
        sa.Column('branch_id', mysql.VARCHAR(length=36), nullable=False),
        sa.Column('value', sa.String(length=256), nullable=False),
        sa.Column('share', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ['branch_id'], ['virtual_tag_rule_branch.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'virtual_tag_value_limit',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.Integer(), nullable=False),
        sa.Column('deleted_at', sa.Integer(), nullable=False),
        sa.Column('virtual_tag_id', mysql.VARCHAR(length=36), nullable=False),
        sa.Column('value', sa.String(length=256), nullable=False),
        sa.Column('limit', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['virtual_tag_id'], ['virtual_tag.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'virtual_tag_id', 'value', 'deleted_at',
            name='uc_virtual_tag_value_limit_vt_value_del'),
    )


def downgrade():
    op.drop_table('virtual_tag_value_limit')
    op.drop_table('virtual_tag_rule_allocation')
    op.drop_table('virtual_tag_rule_condition')
    op.drop_table('virtual_tag_rule_branch')
    op.drop_table('virtual_tag_rule')
    op.drop_table('virtual_tag')
    op.execute('DROP TYPE IF EXISTS virtualtagmodes')
