"""snowflake_cloud_type

Revision ID: b7e2c9a14f01
Revises: c1d4f7a92b08
Create Date: 2026-07-21 16:35:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone

# revision identifiers, used by Alembic.
revision = 'b7e2c9a14f01'
down_revision = 'c1d4f7a92b08'
branch_labels = None
depends_on = None

old_cloud_types = sa.Enum(
    'AWS_CNR', 'ALIBABA_CNR', 'AZURE_CNR', 'AZURE_TENANT',
    'KUBERNETES_CNR', 'ENVIRONMENT', 'GCP_CNR', 'GCP_TENANT',
    'NEBIUS', 'DATABRICKS')
new_cloud_types = sa.Enum(
    'AWS_CNR', 'ALIBABA_CNR', 'AZURE_CNR', 'AZURE_TENANT',
    'KUBERNETES_CNR', 'ENVIRONMENT', 'GCP_CNR', 'GCP_TENANT',
    'NEBIUS', 'DATABRICKS', 'SNOWFLAKE')


def upgrade():
    op.alter_column('cloudaccount', 'type', existing_type=old_cloud_types,
                    type_=new_cloud_types, nullable=False)


def downgrade():
    ct = sa.sql.table('cloudaccount', sa.sql.column('type', new_cloud_types),
                      sa.sql.column('deleted_at', sa.Integer()))
    op.execute(
        ct.update().where(ct.c.type.in_(['SNOWFLAKE'])).values(
            type='ENVIRONMENT', deleted_at=int(datetime.now(
                tz=timezone.utc).timestamp())
        )
    )
    op.alter_column('cloudaccount', 'type', existing_type=new_cloud_types,
                    type_=old_cloud_types, nullable=False)
