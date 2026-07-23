import logging

from diworker.diworker.migrations.base import BaseMigration

"""
Unique partial index for Snowflake raw expenses.

Prevents duplicate rows on concurrent upserts for the Snowflake importer
unique key: cloud_account_id + service_type + resource_id + start_date.
"""

LOG = logging.getLogger(__name__)
INDEX_NAME = 'SnowflakeRawUnique'
INDEX_FIELDS = [
    'cloud_account_id',
    'service_type',
    'resource_id',
    'start_date',
]
PARTIAL_FILTER_EXPRESSION = {
    'cloud_account_id': {'$exists': True},
    'service_type': {'$exists': True},
    'resource_id': {'$exists': True},
    'start_date': {'$exists': True},
}
DEDUP_CHUNK = 5000


class Migration(BaseMigration):
    @property
    def mongo_raw(self):
        return self.db.raw_expenses

    def _deduplicate(self):
        LOG.info(
            'Deduplicating Snowflake-like raw expenses before unique index')
        cursor = self.mongo_raw.aggregate([
            {'$match': PARTIAL_FILTER_EXPRESSION},
            {'$group': {
                '_id': {
                    'cloud_account_id': '$cloud_account_id',
                    'service_type': '$service_type',
                    'resource_id': '$resource_id',
                    'start_date': '$start_date',
                },
                'ids': {'$push': '$_id'},
                'n': {'$sum': 1},
            }},
            {'$match': {'n': {'$gt': 1}}},
        ], allowDiskUse=True)

        to_delete = []
        groups = 0
        deleted = 0
        for group in cursor:
            groups += 1
            # Keep the first id, drop the rest.
            to_delete.extend(group['ids'][1:])
            if len(to_delete) >= DEDUP_CHUNK:
                result = self.mongo_raw.delete_many(
                    {'_id': {'$in': to_delete}})
                deleted += result.deleted_count
                to_delete = []
        if to_delete:
            result = self.mongo_raw.delete_many({'_id': {'$in': to_delete}})
            deleted += result.deleted_count
        LOG.info(
            'Snowflake raw dedupe finished: groups=%s deleted=%s',
            groups, deleted)

    def _create_unique_index(self):
        indexes = self.mongo_raw.index_information()
        if INDEX_NAME in indexes:
            LOG.info('Index %s already exists', INDEX_NAME)
            return
        LOG.info('Creating unique index %s', INDEX_NAME)
        self.mongo_raw.create_index(
            [(field, 1) for field in INDEX_FIELDS],
            name=INDEX_NAME,
            unique=True,
            background=True,
            partialFilterExpression=PARTIAL_FILTER_EXPRESSION,
        )

    def upgrade(self):
        self._deduplicate()
        self._create_unique_index()

    def downgrade(self):
        indexes = self.mongo_raw.index_information()
        if INDEX_NAME in indexes:
            LOG.info('Dropping index %s', INDEX_NAME)
            self.mongo_raw.drop_index(INDEX_NAME)
