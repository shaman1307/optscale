import json

from rest_api.rest_api_server.controllers.sso_oidc import SsoOidcAsyncController
from rest_api.rest_api_server.handlers.v1.base import BaseAuthHandler
from rest_api.rest_api_server.handlers.v1.base_async import (
    BaseAsyncCollectionHandler)
from rest_api.rest_api_server.handlers.v2.base import BaseHandler
from rest_api.rest_api_server.utils import ModelEncoder, run_task


class SsoLoginConfigAsyncHandler(BaseAsyncCollectionHandler, BaseAuthHandler,
                                 BaseHandler):
    def _get_controller_class(self):
        return SsoOidcAsyncController

    def prepare(self):
        self.set_content_type()

    async def get(self):
        """
        ---
        description: |
            Public OIDC login settings for the sign-in page.
            Client secret is omitted unless CLUSTER_SECRET is sent with
            include_secret=true.
        tags: [sso_login_config]
        summary: Get public SSO/OIDC login config
        parameters:
        -   name: include_secret
            in: query
            required: false
            type: boolean
            description: Return client_secret (cluster secret required)
        responses:
            200:
                description: SSO login config
                schema:
                    type: object
                    properties:
                        configured:
                            type: boolean
                        enabled:
                            type: boolean
                        issuer:
                            type: string
                        client_id:
                            type: string
                        authorization_endpoint:
                            type: string
                        login_only:
                            type: boolean
                        client_secret:
                            type: string
        """
        include_secret = self.get_arg('include_secret', bool, False)
        if include_secret and not self.check_cluster_secret(raises=False):
            include_secret = False
        res = await run_task(self.controller.get_login_config, include_secret)
        self.write(json.dumps(res, cls=ModelEncoder))
