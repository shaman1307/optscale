import logging

from optscale_client.rest_api_client.client_v2 import Client as RestClient
from pymongo import UpdateOne

from diworker.diworker.migrations.base import BaseMigration

"""
Backfill GCP billing resource display names from Terraform/user label "name".

Only renames SKU-shaped billing resources (not discovered numeric IDs / API
names like vmoptscale-boot). Tag keys are stored base64-encoded in Mongo.
"""

LOG = logging.getLogger(__name__)
# base64.b64encode(b'name').decode()
TAG_NAME_KEY = 'bmFtZQ=='
CHUNK_SIZE = 1000
# Match OptScale-generated billing names / cloud_resource_ids from SKU+region.
SKU_SHAPED_PATTERN = (
    r'( running in |Licensing Fee|PD Capacity|Network |IP Charge|'
    r'Committed Use|VM state:|Data Transfer|Storage )'
)


class Migration(BaseMigration):
    @property
    def mongo_resources(self):
        return self.db.resources

    @property
    def rest_cl(self):
        if self._rest_cl is None:
            self._rest_cl = RestClient(
                url=self.config_cl.restapi_url(),
                secret=self.config_cl.cluster_secret())
        return self._rest_cl

    def get_gcp_cloud_account_ids(self):
        cloud_account_ids = set()
        _, organizations = self.rest_cl.organization_list({
            'with_connected_accounts': True, 'is_demo': False})
        for org in organizations['organizations']:
            _, accounts = self.rest_cl.cloud_account_list(
                org['id'], type='gcp_cnr')
            for cloud_account in accounts['cloud_accounts']:
                cloud_account_ids.add(cloud_account['id'])
        return cloud_account_ids

    def _match_filter(self, cloud_acc_id):
        return {
            'cloud_account_id': cloud_acc_id,
            'deleted_at': 0,
            f'tags.{TAG_NAME_KEY}': {'$exists': True, '$nin': [None, '']},
            '$or': [
                {'name': {'$regex': SKU_SHAPED_PATTERN, '$options': 'i'}},
                {'cloud_resource_id': {
                    '$regex': SKU_SHAPED_PATTERN, '$options': 'i'}},
            ],
        }

    def upgrade(self):
        cloud_accs = self.get_gcp_cloud_account_ids()
        total = 0
        for i, cloud_acc_id in enumerate(cloud_accs):
            LOG.info(
                'Renaming GCP billing resources from tags.name for %s (%s/%s)',
                cloud_acc_id, i + 1, len(cloud_accs))
            ops = []
            modified = 0
            cursor = self.mongo_resources.find(
                self._match_filter(cloud_acc_id),
                {'_id': 1, 'name': 1, f'tags.{TAG_NAME_KEY}': 1},
            )
            for doc in cursor:
                tag_name = (doc.get('tags') or {}).get(TAG_NAME_KEY)
                if not tag_name or tag_name == doc.get('name'):
                    continue
                ops.append(UpdateOne(
                    {'_id': doc['_id']},
                    {'$set': {'name': tag_name}},
                ))
                if len(ops) >= CHUNK_SIZE:
                    result = self.mongo_resources.bulk_write(ops, ordered=False)
                    modified += result.modified_count
                    ops = []
            if ops:
                result = self.mongo_resources.bulk_write(ops, ordered=False)
                modified += result.modified_count
            total += modified
            if modified:
                LOG.info(
                    'Updated %s resources for cloud account %s',
                    modified, cloud_acc_id)
        LOG.info('GCP name-from-tag backfill done, updated %s resources', total)

    def downgrade(self):
        pass
