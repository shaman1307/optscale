import logging
from collections import defaultdict

from sqlalchemy import and_

import tools.optscale_time as opttime
from tools.cloud_adapter.gcp_resource_collapse import collapse_group_id
from tools.optscale_exceptions.common_exc import (
    NotFoundException, WrongArgumentsException)

from rest_api.rest_api_server.controllers.base import BaseController, MongoMixin
from rest_api.rest_api_server.controllers.base_async import (
    BaseAsyncControllerWrapper)
from rest_api.rest_api_server.exceptions import Err
from rest_api.rest_api_server.models.enums import CloudTypes
from rest_api.rest_api_server.models.models import CloudAccount, Organization

LOG = logging.getLogger(__name__)

SUPPORTED_TYPES = {CloudTypes.GCP_CNR, CloudTypes.GCP_TENANT}


class ResourceDuplicatesController(BaseController, MongoMixin):
    def _get_model_type(self):
        return CloudAccount

    @property
    def resource_duplicate_checks_collection(self):
        return self.mongo_client.restapi.resource_duplicate_checks

    def _get_cloud_account(self, cloud_account_id):
        data_set = self.session.query(
            CloudAccount, Organization
        ).outerjoin(Organization, and_(
            Organization.id == CloudAccount.organization_id,
            Organization.deleted.is_(False)
        )).filter(and_(
            CloudAccount.id == cloud_account_id,
            CloudAccount.deleted.is_(False)
        )).one_or_none()
        if not data_set:
            raise NotFoundException(
                Err.OE0002, [CloudAccount.__name__, cloud_account_id])
        cloud_acc, org = data_set
        if not org:
            raise NotFoundException(
                Err.OE0002,
                [Organization.__name__, cloud_acc.organization_id])
        return cloud_acc

    def _resolve_scope(self, cloud_acc):
        if cloud_acc.type not in SUPPORTED_TYPES:
            raise WrongArgumentsException(
                Err.OE0436, [cloud_acc.type.value])
        if cloud_acc.type == CloudTypes.GCP_TENANT:
            children = self.session.query(CloudAccount).filter(and_(
                CloudAccount.parent_id == cloud_acc.id,
                CloudAccount.deleted.is_(False),
                CloudAccount.type == CloudTypes.GCP_CNR,
            )).all()
            account_ids = [c.id for c in children]
            names = {c.id: c.name for c in children}
            return account_ids, names
        return [cloud_acc.id], {cloud_acc.id: cloud_acc.name}

    def _find_duplicate_groups(self, cloud_account_ids, names_by_id):
        if not cloud_account_ids:
            return []
        cursor = self.resources_collection.find({
            'cloud_account_id': {'$in': cloud_account_ids},
            'deleted_at': 0,
            'cluster_type_id': {'$exists': False},
            'cloud_resource_id': {'$exists': True, '$nin': [None, '']},
        }, {
            '_id': 1,
            'name': 1,
            'resource_type': 1,
            'cloud_account_id': 1,
            'cloud_resource_id': 1,
            'resource_global_name': 1,
            'tags': 1,
        })
        grouped = defaultdict(list)
        for doc in cursor:
            group_id = collapse_group_id(doc)
            if not group_id:
                continue
            grouped[(doc.get('cloud_account_id'), group_id)].append(doc)
        groups = []
        for (ca_id, group_id), docs in grouped.items():
            if len(docs) < 2:
                continue
            resources = []
            for res in docs:
                resources.append({
                    'id': str(res.get('_id')),
                    'name': res.get('name'),
                    'resource_type': res.get('resource_type'),
                    'cloud_account_id': res.get('cloud_account_id'),
                })
            groups.append({
                'cloud_account_id': ca_id,
                'cloud_account_name': names_by_id.get(ca_id),
                'cloud_resource_id': group_id,
                'count': len(docs),
                'resources': resources,
            })
        groups.sort(key=lambda row: (-row['count'], row['cloud_resource_id']))
        return groups

    def _build_result(self, cloud_account_id, groups, checked_at=None):
        return {
            'cloud_account_id': cloud_account_id,
            'count': len(groups),
            'checked_at': checked_at if checked_at is not None
            else opttime.utcnow_timestamp(),
            'duplicate_groups': groups,
        }

    def snapshot_counts_by_account(self, cloud_account_ids):
        """Duplicate-group counts from snapshots, attributed to projects.

        Tenant snapshots store groups with per-project cloud_account_id; those
        are counted on the child so Data Sources can highlight the project.
        """
        ids = list(cloud_account_ids or [])
        counts = {cid: 0 for cid in ids}
        if not ids:
            return counts
        cursor = self.resource_duplicate_checks_collection.find(
            {'cloud_account_id': {'$in': ids}},
            {'_id': 0, 'cloud_account_id': 1, 'count': 1,
             'duplicate_groups': 1},
        )
        for doc in cursor:
            parent_id = doc.get('cloud_account_id')
            groups = doc.get('duplicate_groups') or []
            if groups:
                per_account = {}
                for group in groups:
                    cid = group.get('cloud_account_id') or parent_id
                    per_account[cid] = per_account.get(cid, 0) + 1
                for cid, n in per_account.items():
                    counts[cid] = max(counts.get(cid, 0), n)
                if parent_id:
                    counts[parent_id] = max(
                        counts.get(parent_id, 0), len(groups))
            elif parent_id:
                counts[parent_id] = max(
                    counts.get(parent_id, 0), int(doc.get('count') or 0))
        return counts

    def get(self, cloud_account_id):
        cloud_acc = self._get_cloud_account(cloud_account_id)
        account_ids, names = self._resolve_scope(cloud_acc)
        groups = self._find_duplicate_groups(account_ids, names)
        return self._build_result(cloud_account_id, groups)

    def refresh(self, cloud_account_id):
        result = self.get(cloud_account_id)
        self.resource_duplicate_checks_collection.update_one(
            {'cloud_account_id': cloud_account_id},
            {'$set': {
                'cloud_account_id': cloud_account_id,
                'count': result['count'],
                'checked_at': result['checked_at'],
                'duplicate_groups': result['duplicate_groups'],
            }},
            upsert=True,
        )
        if result['count']:
            LOG.warning(
                'Found %s resource duplicate group(s) for cloud account %s',
                result['count'], cloud_account_id)
        return result


class ResourceDuplicatesAsyncController(BaseAsyncControllerWrapper):
    def _get_controller_class(self):
        return ResourceDuplicatesController
