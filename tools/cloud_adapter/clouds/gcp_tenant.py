from datetime import datetime, timedelta, timezone
from functools import cached_property
from tools.cloud_adapter.clouds.gcp import Gcp, DEFAULT_KWARGS, BILLING_THRESHOLD
from tools.cloud_adapter.enums import CloudTypes
from tools.cloud_adapter.utils import CloudParameter
from tools.cloud_adapter.exceptions import (
    InvalidParameterException, CloudConnectionError)
from google.cloud import bigquery
from google.api_core import exceptions as api_exceptions


class GcpTenant(Gcp):
    BILLING_CREDS = [
        CloudParameter(
            name="billing_data",
            type=dict,
            required=True,
            dependencies=[
                CloudParameter(name="project_id", type=str, required=False),
                CloudParameter(name="dataset_name", type=str, required=True),
                CloudParameter(name="table_name", type=str, required=True),
                CloudParameter(
                    name="resource_table_name", type=str, required=False),
            ],
        ),
        CloudParameter(
            name="pricing_data",
            type=dict,
            required=False,
            dependencies=[
                CloudParameter(name="project_id", type=str, required=False),
                CloudParameter(name="dataset_name", type=str, required=True),
                CloudParameter(name="table_name", type=str, required=True),
            ],
        ),
        CloudParameter(name="credentials", type=dict, required=True, protected=True),

        # Service parameters
        CloudParameter(name='skipped_subscriptions', type=dict, required=False),
        CloudParameter(name='last_children_sync_at', type=int, required=False),
    ]

    @classmethod
    def configure_credentials(cls, config):
        project_id = config['credentials'].pop('project_id', None)
        if project_id:
            for k in ['billing_data', 'pricing_data']:
                dataset = config.get(k)
                if dataset and not dataset.get('project_id'):
                    config[k]['project_id'] = project_id
        return config

    @cached_property
    def bigquery_client(self):
        return bigquery.Client.from_service_account_info(
            self.credentials,
            project=self.billing_project_id,
        )

    def _test_bigquery_connection(self):
        self._list_projects()

    def discovery_calls_map(self):
        return {}

    def _validate_credentials(self):
        if "client_id" not in self.credentials:
            raise InvalidParameterException(
                "Credentials should contain 'client_id'"
            )

    def validate_credentials(self, org_id=None):
        try:
            self._validate_billing_config()
            self._validate_billing_type()
            self._validate_credentials()
            self._test_bigquery_connection()
        except api_exceptions.Forbidden as ex:
            # remove new-lines, otherwise tornado will fail to write response
            raise InvalidParameterException(str(ex).replace("\n", " "))
        except Exception as ex:
            raise CloudConnectionError(str(ex))
        return {"account_id": self.credentials['client_id'], "warnings": []}

    def _list_null_project_buckets(self, lookback_days: int = None):
        """Distinct COALESCE(project.name, service.description) for null ids.

        Same short _PARTITIONTIME window as _list_projects (BILLING_THRESHOLD).
        """
        if lookback_days is None:
            lookback_days = BILLING_THRESHOLD
        end = datetime.now(tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)
        start = end - timedelta(days=lookback_days)
        months = self.invoice_months_for_window(start, end)
        if not months:
            return []
        months_sql = ', '.join(f'"{m}"' for m in months)
        query = f"""
            SELECT DISTINCT
                COALESCE(project.name, service.description) AS bucket_name
            FROM `{self._billing_table_full_name()}`
            WHERE invoice.month IN ({months_sql})
              AND _PARTITIONTIME >= TIMESTAMP("{start}")
              AND _PARTITIONTIME < TIMESTAMP("{end}")
              AND project.id IS NULL
              AND COALESCE(project.name, service.description) IS NOT NULL
            """
        return [
            row[0]
            for row in self.bigquery_client.query(
                query, **DEFAULT_KWARGS).result()
            if row[0]
        ]

    def _has_unassigned_billing_rows(self) -> bool:
        """True if billing export has rows with null project.id."""
        return bool(self._list_null_project_buckets())

    def _list_projects(self):
        dt = self._get_billing_threshold_date()
        # Real projects: project.id / project.name.
        # Null project.id: virtual CA named like the billing view —
        # COALESCE(project.name, service.description) → e.g. Compute Engine.
        prefix = self.VIRTUAL_SERVICE_PREFIX
        query = f"""
            SELECT
                CASE
                    WHEN project.id IS NULL THEN CONCAT(
                        "{prefix}",
                        COALESCE(project.name, service.description))
                    ELSE project.id
                END AS project_id,
                CASE
                    WHEN project.id IS NULL THEN COALESCE(
                        project.name, service.description)
                    ELSE project.name
                END AS project_name,
                max(export_time)
            FROM `{self._billing_table_full_name()}`
            WHERE TIMESTAMP_TRUNC(_PARTITIONTIME, DAY) >= TIMESTAMP("{dt}")
            GROUP BY project_id, project_name
            """
        query_job = self.bigquery_client.query(query, **DEFAULT_KWARGS)
        names_map = {}
        for r in list(query_job.result()):
            project_id, name, export_dt = r
            if not project_id or (
                project_id in names_map and names_map[project_id][1] > export_dt
            ):
                continue
            if self.parse_virtual_service(project_id):
                # Keep display name = service/bucket, not the prefixed id.
                name = self.parse_virtual_service(project_id)
            names_map[project_id] = (name or project_id, export_dt)
        # BILLING_THRESHOLD window may miss older null-project partitions.
        for bucket in self._list_null_project_buckets():
            pid = self.virtual_service_project_id(bucket)
            if pid not in names_map:
                names_map[pid] = (bucket, None)
        return {k: v[0] for k, v in names_map.items()}

    def get_children_configs(self):
        projects = self._list_projects()
        return [{
            'name': project_name,
            'config': {
                'project_id': project_id
            },
            'type': CloudTypes.GCP_CNR.value
        } for project_id, project_name in projects.items()]
