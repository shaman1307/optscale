"""snowflake_tenant_cloud_type

Revision ID: d4e8f1a70b22
Revises: c8f3a1b92e04
Create Date: 2026-07-31 00:15:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone

# revision identifiers, used by Alembic.
revision = 'd4e8f1a70b22'
down_revision = 'c8f3a1b92e04'
branch_labels = None
depends_on = None

old_cloud_types = sa.Enum(
    'AWS_CNR', 'ALIBABA_CNR', 'AZURE_CNR', 'AZURE_TENANT',
    'KUBERNETES_CNR', 'ENVIRONMENT', 'GCP_CNR', 'GCP_TENANT',
    'NEBIUS', 'DATABRICKS', 'SNOWFLAKE')
new_cloud_types = sa.Enum(
    'AWS_CNR', 'ALIBABA_CNR', 'AZURE_CNR', 'AZURE_TENANT',
    'KUBERNETES_CNR', 'ENVIRONMENT', 'GCP_CNR', 'GCP_TENANT',
    'NEBIUS', 'DATABRICKS', 'SNOWFLAKE', 'SNOWFLAKE_TENANT')


def upgrade():
    op.alter_column('cloudaccount', 'type', existing_type=old_cloud_types,
                    type_=new_cloud_types, nullable=False)


def downgrade():
    ct = sa.sql.table('cloudaccount', sa.sql.column('type', new_cloud_types),
                      sa.sql.column('deleted_at', sa.Integer()))
    op.execute(
        ct.update().where(ct.c.type.in_(['SNOWFLAKE_TENANT'])).values(
            type='SNOWFLAKE', deleted_at=int(datetime.now(
                tz=timezone.utc).timestamp())
        )
    )
    op.alter_column('cloudaccount', 'type', existing_type=new_cloud_types,
                    type_=old_cloud_types, nullable=False)
