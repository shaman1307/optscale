import json

from rest_api.rest_api_server.controllers.resource_duplicates import (
    ResourceDuplicatesAsyncController)
from rest_api.rest_api_server.handlers.v1.base import BaseAuthHandler
from rest_api.rest_api_server.handlers.v1.base_async import (
    BaseAsyncCollectionHandler)
from rest_api.rest_api_server.handlers.v2.base import BaseHandler
from rest_api.rest_api_server.utils import run_task, ModelEncoder


class ResourceDuplicatesAsyncCollectionHandler(BaseAsyncCollectionHandler,
                                               BaseAuthHandler,
                                               BaseHandler):
    def _get_controller_class(self):
        return ResourceDuplicatesAsyncController

    def post(self, *args, **kwargs):
        # POST is implemented as refresh below; disable default create.
        self.raise405()

    async def get(self, cloud_account_id):
        """
        ---
        description: |
            Get duplicate and uncollapsed GCP resources for a cloud account.
            Groups live Mongo docs that share a cloud_resource_id, or that
            share a Dataproc/Composer/GKE collapse keeper while leftovers
            still keep sku.id / discovery ids. For gcp_tenant aggregates
            across all child projects.
            Required permission: INFO_ORGANIZATION or CLUSTER_SECRET
        tags: [resource_duplicates]
        summary: List resource duplicate groups
        parameters:
        -   name: cloud_account_id
            in: path
            description: Cloud account id (gcp_cnr or gcp_tenant)
            required: true
            type: string
        responses:
            200:
                description: Resource duplicates
                schema:
                    type: object
                    properties:
                        cloud_account_id:
                            type: string
                        count:
                            type: integer
                            description: Number of duplicate groups
                        checked_at:
                            type: integer
                        duplicate_groups:
                            type: array
                            items:
                                type: object
                                properties:
                                    cloud_account_id:
                                        type: string
                                    cloud_account_name:
                                        type: string
                                    cloud_resource_id:
                                        type: string
                                    count:
                                        type: integer
                                    resources:
                                        type: array
                                        items:
                                            type: object
            400:
                description: |
                    Wrong arguments:
                    - OE0436: Cloud account type is not supported
            401:
                description: |
                    Unauthorized:
                    - OE0235: Unauthorized
                    - OE0237: This resource requires authorization
            403:
                description: |
                    Forbidden:
                    - OE0234: Forbidden
            404:
                description: |
                    Not found:
                    - OE0002: Cloud account not found
        security:
        - secret: []
        - token: []
        """
        if not self.check_cluster_secret(raises=False):
            await self.check_permissions(
                'INFO_ORGANIZATION', 'cloud_account', cloud_account_id)
        res = await run_task(self.controller.get, cloud_account_id)
        self.write(json.dumps(res, cls=ModelEncoder))

    async def put(self, cloud_account_id):
        """
        ---
        description: |
            Recompute and persist resource duplicates snapshot for a GCP
            cloud account. Used by resource discovery after a successful run.
            Required permission: CLUSTER_SECRET
        tags: [resource_duplicates]
        summary: Refresh resource duplicates snapshot
        parameters:
        -   name: cloud_account_id
            in: path
            description: Cloud account id (gcp_cnr or gcp_tenant)
            required: true
            type: string
        responses:
            200:
                description: Updated resource duplicates snapshot
            400:
                description: |
                    Wrong arguments:
                    - OE0436: Cloud account type is not supported
            401:
                description: |
                    Unauthorized:
                    - OE0235: Unauthorized
                    - OE0237: This resource requires authorization
            403:
                description: |
                    Forbidden:
                    - OE0234: Forbidden
            404:
                description: |
                    Not found:
                    - OE0002: Cloud account not found
        security:
        - secret: []
        """
        self.check_cluster_secret()
        res = await run_task(self.controller.refresh, cloud_account_id)
        self.write(json.dumps(res, cls=ModelEncoder))
