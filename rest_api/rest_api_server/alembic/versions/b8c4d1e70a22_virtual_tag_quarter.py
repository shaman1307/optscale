"""virtual_tag_quarter

Revision ID: b8c4d1e70a22
Revises: e7a1c4b92f10
Create Date: 2026-08-20 16:30:00.000000

"""
import logging
import os
import uuid
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = 'b8c4d1e70a22'
down_revision = 'e7a1c4b92f10'
branch_labels = None
depends_on = None

LOG = logging.getLogger(__name__)
SOURCE_QUARTER = '2026Q3'
TARGET_QUARTER = '2026Q2'
DEFAULT_ETCD_HOST = 'etcd-client'
DEFAULT_ETCD_PORT = 80


def _clone_virtual_tag_defs(conn):
    sources = conn.execute(text(
        'SELECT id, created_at, deleted_at, organization_id, `key`, name, '
        'mode, source_tag_key FROM virtual_tag '
        'WHERE quarter = :source AND deleted_at = 0'
    ), {'source': SOURCE_QUARTER}).fetchall()
    for source in sources:
        source_id = source[0]
        exists = conn.execute(text(
            'SELECT id FROM virtual_tag WHERE organization_id = :org_id '
            'AND `key` = :key AND quarter = :target AND deleted_at = 0'
        ), {
            'org_id': source[3],
            'key': source[4],
            'target': TARGET_QUARTER,
        }).fetchone()
        if exists:
            continue
        new_vt_id = str(uuid.uuid4())
        conn.execute(text(
            'INSERT INTO virtual_tag (id, created_at, deleted_at, '
            'organization_id, `key`, name, mode, source_tag_key, quarter) '
            'VALUES (:id, :created_at, :deleted_at, :organization_id, :key, '
            ':name, :mode, :source_tag_key, :quarter)'
        ), {
            'id': new_vt_id,
            'created_at': source[1],
            'deleted_at': source[2],
            'organization_id': source[3],
            'key': source[4],
            'name': source[5],
            'mode': source[6],
            'source_tag_key': source[7],
            'quarter': TARGET_QUARTER,
        })
        limits = conn.execute(text(
            'SELECT created_at, deleted_at, value, `limit` '
            'FROM virtual_tag_value_limit '
            'WHERE virtual_tag_id = :vt_id AND deleted_at = 0'
        ), {'vt_id': source_id}).fetchall()
        for limit in limits:
            conn.execute(text(
                'INSERT INTO virtual_tag_value_limit (id, created_at, '
                'deleted_at, virtual_tag_id, value, `limit`) '
                'VALUES (:id, :created_at, :deleted_at, :virtual_tag_id, '
                ':value, :limit_val)'
            ), {
                'id': str(uuid.uuid4()),
                'created_at': limit[0],
                'deleted_at': limit[1],
                'virtual_tag_id': new_vt_id,
                'value': limit[2],
                'limit_val': limit[3],
            })
        rules = conn.execute(text(
            'SELECT id, created_at, deleted_at, organization_id, name, '
            'priority, active, creator_id FROM virtual_tag_rule '
            'WHERE virtual_tag_id = :vt_id AND deleted_at = 0'
        ), {'vt_id': source_id}).fetchall()
        for rule in rules:
            new_rule_id = str(uuid.uuid4())
            conn.execute(text(
                'INSERT INTO virtual_tag_rule (id, created_at, deleted_at, '
                'organization_id, virtual_tag_id, name, priority, active, '
                'creator_id) VALUES (:id, :created_at, :deleted_at, '
                ':organization_id, :virtual_tag_id, :name, :priority, '
                ':active, :creator_id)'
            ), {
                'id': new_rule_id,
                'created_at': rule[1],
                'deleted_at': rule[2],
                'organization_id': rule[3],
                'virtual_tag_id': new_vt_id,
                'name': rule[4],
                'priority': rule[5],
                'active': rule[6],
                'creator_id': rule[7],
            })
            branches = conn.execute(text(
                'SELECT id, created_at, deleted_at, priority '
                'FROM virtual_tag_rule_branch '
                'WHERE rule_id = :rule_id AND deleted_at = 0'
            ), {'rule_id': rule[0]}).fetchall()
            for branch in branches:
                new_branch_id = str(uuid.uuid4())
                conn.execute(text(
                    'INSERT INTO virtual_tag_rule_branch (id, created_at, '
                    'deleted_at, rule_id, priority) VALUES ('
                    ':id, :created_at, :deleted_at, :rule_id, :priority)'
                ), {
                    'id': new_branch_id,
                    'created_at': branch[1],
                    'deleted_at': branch[2],
                    'rule_id': new_rule_id,
                    'priority': branch[3],
                })
                conditions = conn.execute(text(
                    'SELECT created_at, deleted_at, type, meta_info '
                    'FROM virtual_tag_rule_condition '
                    'WHERE branch_id = :branch_id AND deleted_at = 0'
                ), {'branch_id': branch[0]}).fetchall()
                for cond in conditions:
                    conn.execute(text(
                        'INSERT INTO virtual_tag_rule_condition ('
                        'id, created_at, deleted_at, type, branch_id, '
                        'meta_info) VALUES (:id, :created_at, :deleted_at, '
                        ':cond_type, :branch_id, :meta_info)'
                    ), {
                        'id': str(uuid.uuid4()),
                        'created_at': cond[0],
                        'deleted_at': cond[1],
                        'cond_type': cond[2],
                        'branch_id': new_branch_id,
                        'meta_info': cond[3],
                    })
                allocations = conn.execute(text(
                    'SELECT created_at, deleted_at, value, share '
                    'FROM virtual_tag_rule_allocation '
                    'WHERE branch_id = :branch_id AND deleted_at = 0'
                ), {'branch_id': branch[0]}).fetchall()
                for alloc in allocations:
                    conn.execute(text(
                        'INSERT INTO virtual_tag_rule_allocation ('
                        'id, created_at, deleted_at, branch_id, value, share) '
                        'VALUES (:id, :created_at, :deleted_at, :branch_id, '
                        ':value, :share)'
                    ), {
                        'id': str(uuid.uuid4()),
                        'created_at': alloc[0],
                        'deleted_at': alloc[1],
                        'branch_id': new_branch_id,
                        'value': alloc[2],
                        'share': alloc[3],
                    })


def _backfill_mongo_maps():
    try:
        from pymongo import MongoClient
        from optscale_client.config_client.client import Client as EtcdClient
        from rest_api.rest_api_server.controllers.virtual_tag_apply import (
            backfill_legacy_virtual_tags_by_quarter)
    except Exception as exc:
        LOG.warning('Skip Mongo VT backfill import: %s', exc)
        return
    try:
        etcd_host = os.environ.get('HX_ETCD_HOST', DEFAULT_ETCD_HOST)
        etcd_port = os.environ.get('HX_ETCD_PORT', DEFAULT_ETCD_PORT)
        config_cl = EtcdClient(host=etcd_host, port=int(etcd_port))
        mongo_params = config_cl.mongo_params()
        collection = MongoClient(mongo_params[0]).restapi.resources
        updated = backfill_legacy_virtual_tags_by_quarter(collection)
        LOG.info('Backfilled virtual_tags_by_quarter on %s resources', updated)
    except Exception as exc:
        LOG.warning('Skip Mongo VT backfill: %s', exc)


def _delete_quarter_tree(conn, quarter):
    conn.execute(text(
        'DELETE c FROM virtual_tag_rule_condition c '
        'JOIN virtual_tag_rule_branch b ON c.branch_id = b.id '
        'JOIN virtual_tag_rule r ON b.rule_id = r.id '
        'JOIN virtual_tag t ON r.virtual_tag_id = t.id '
        'WHERE t.quarter = :quarter'
    ), {'quarter': quarter})
    conn.execute(text(
        'DELETE a FROM virtual_tag_rule_allocation a '
        'JOIN virtual_tag_rule_branch b ON a.branch_id = b.id '
        'JOIN virtual_tag_rule r ON b.rule_id = r.id '
        'JOIN virtual_tag t ON r.virtual_tag_id = t.id '
        'WHERE t.quarter = :quarter'
    ), {'quarter': quarter})
    conn.execute(text(
        'DELETE b FROM virtual_tag_rule_branch b '
        'JOIN virtual_tag_rule r ON b.rule_id = r.id '
        'JOIN virtual_tag t ON r.virtual_tag_id = t.id '
        'WHERE t.quarter = :quarter'
    ), {'quarter': quarter})
    conn.execute(text(
        'DELETE r FROM virtual_tag_rule r '
        'JOIN virtual_tag t ON r.virtual_tag_id = t.id '
        'WHERE t.quarter = :quarter'
    ), {'quarter': quarter})
    conn.execute(text(
        'DELETE l FROM virtual_tag_value_limit l '
        'JOIN virtual_tag t ON l.virtual_tag_id = t.id '
        'WHERE t.quarter = :quarter'
    ), {'quarter': quarter})
    conn.execute(text(
        'DELETE FROM virtual_tag WHERE quarter = :quarter'
    ), {'quarter': quarter})


def upgrade():
    op.add_column(
        'virtual_tag',
        sa.Column('quarter', sa.String(length=256), nullable=True),
    )
    op.execute("UPDATE virtual_tag SET quarter = '2026Q3' WHERE quarter IS NULL")
    op.alter_column(
        'virtual_tag', 'quarter',
        existing_type=sa.String(length=256),
        nullable=False,
    )
    op.drop_constraint(
        'uc_virtual_tag_key_del_at_org_id', 'virtual_tag', type_='unique')
    op.drop_constraint(
        'uc_virtual_tag_name_del_at_org_id', 'virtual_tag', type_='unique')
    op.create_unique_constraint(
        'uc_virtual_tag_key_q_del_at_org_id',
        'virtual_tag',
        ['key', 'quarter', 'deleted_at', 'organization_id'],
    )
    op.create_unique_constraint(
        'uc_virtual_tag_name_q_del_at_org_id',
        'virtual_tag',
        ['name', 'quarter', 'deleted_at', 'organization_id'],
    )
    _clone_virtual_tag_defs(op.get_bind())
    _backfill_mongo_maps()


def downgrade():
    _delete_quarter_tree(op.get_bind(), TARGET_QUARTER)
    op.drop_constraint(
        'uc_virtual_tag_key_q_del_at_org_id', 'virtual_tag', type_='unique')
    op.drop_constraint(
        'uc_virtual_tag_name_q_del_at_org_id', 'virtual_tag', type_='unique')
    op.create_unique_constraint(
        'uc_virtual_tag_key_del_at_org_id',
        'virtual_tag',
        ['key', 'deleted_at', 'organization_id'],
    )
    op.create_unique_constraint(
        'uc_virtual_tag_name_del_at_org_id',
        'virtual_tag',
        ['name', 'deleted_at', 'organization_id'],
    )
    op.drop_column('virtual_tag', 'quarter')
