import json

from rest_api.rest_api_server.controllers.virtual_tag import (
    VirtualTagAsyncController, VirtualTagRuleAsyncController)
from rest_api.rest_api_server.controllers.virtual_tag_apply import (
    VirtualTagApplyAsyncController)
from rest_api.rest_api_server.handlers.v1.base_async import (
    BaseAsyncCollectionHandler, BaseAsyncItemHandler)
from rest_api.rest_api_server.handlers.v1.base import BaseAuthHandler
from rest_api.rest_api_server.handlers.v2.base import BaseHandler
from rest_api.rest_api_server.utils import (
    run_task, ModelEncoder, check_bool_attribute, check_string_attribute)


class VirtualTagAsyncCollectionHandler(BaseAsyncCollectionHandler,
                                       BaseAuthHandler, BaseHandler):
    def _get_controller_class(self):
        return VirtualTagAsyncController

    async def post(self, organization_id, **url_params):
        """
        ---
        description: |
            Create a virtual tag
            Required permission: EDIT_PARTNER
        tags: [virtual_tags]
        summary: Create virtual tag
        """
        await self.check_permissions(
            'EDIT_PARTNER', 'organization', organization_id)
        data = self._request_body()
        data['organization_id'] = organization_id
        res = await run_task(self.controller.create, **data)
        self.set_status(201)
        self.write(json.dumps(res, cls=ModelEncoder))

    async def get(self, organization_id):
        """
        ---
        description: |
            List virtual tags
            Required permission: INFO_ORGANIZATION
        tags: [virtual_tags]
        summary: List virtual tags
        """
        await self.check_permissions(
            'INFO_ORGANIZATION', 'organization', organization_id)
        res = await run_task(
            self.controller.list, organization_id,
            quarter=self.get_arg('quarter', str),
            name=self.get_arg('name', str),
            value=self.get_arg('value', str),
            cloud_account_id=self.get_arg(
                'cloud_account_id', str, repeated=True),
        )
        self.write(json.dumps(res, cls=ModelEncoder))


class VirtualTagCopyAsyncHandler(BaseAsyncCollectionHandler,
                                 BaseAuthHandler, BaseHandler):
    def _get_controller_class(self):
        return VirtualTagAsyncController

    async def post(self, organization_id, **url_params):
        await self.check_permissions(
            'EDIT_PARTNER', 'organization', organization_id)
        data = self._request_body()
        res = await run_task(
            self.controller.copy_quarter, organization_id,
            data.get('source_quarter'), data.get('target_quarter'))
        self.write(json.dumps(res, cls=ModelEncoder))


class VirtualTagResourceOptionsAsyncHandler(BaseAsyncCollectionHandler,
                                            BaseAuthHandler, BaseHandler):
    def _get_controller_class(self):
        return VirtualTagAsyncController

    async def get(self, organization_id):
        await self.check_permissions(
            'INFO_ORGANIZATION', 'organization', organization_id)
        res = await run_task(
            self.controller.search_resources, organization_id,
            search=self.get_arg('search', str),
            cloud_account_id=self.get_arg(
                'cloud_account_id', str, repeated=True),
            limit=self.get_arg('limit', int),
        )
        self.write(json.dumps(res, cls=ModelEncoder))


class VirtualTagAsyncItemHandler(BaseAsyncItemHandler, BaseAuthHandler,
                                 BaseHandler):
    def _get_controller_class(self):
        return VirtualTagAsyncController

    async def get(self, id, **kwargs):
        """
        ---
        description: |
            Get virtual tag
            Required permission: INFO_ORGANIZATION
        tags: [virtual_tags]
        summary: Get virtual tag
        """
        item = await run_task(self.controller.get_info, id)
        await self.check_permissions(
            'INFO_ORGANIZATION', 'organization', item['organization_id'])
        self.write(json.dumps(item, cls=ModelEncoder))

    async def patch(self, id, **kwargs):
        """
        ---
        description: |
            Update virtual tag
            Required permission: EDIT_PARTNER
        tags: [virtual_tags]
        summary: Update virtual tag
        """
        item = await run_task(self.controller.get_info, id)
        await self.check_permissions(
            'EDIT_PARTNER', 'organization', item['organization_id'])
        data = self._request_body()
        res = await run_task(self.controller.edit, id, **data)
        self.write(json.dumps(res, cls=ModelEncoder))

    async def delete(self, id, **kwargs):
        """
        ---
        description: |
            Delete virtual tag
            Required permission: EDIT_PARTNER
        tags: [virtual_tags]
        summary: Delete virtual tag
        """
        item = await run_task(self.controller.get_info, id)
        await self.check_permissions(
            'EDIT_PARTNER', 'organization', item['organization_id'])
        await run_task(self.controller.delete, id)
        self.set_status(204)


class VirtualTagValueLimitsAsyncHandler(BaseAsyncItemHandler, BaseAuthHandler,
                                        BaseHandler):
    def _get_controller_class(self):
        return VirtualTagAsyncController

    async def put(self, id, **kwargs):
        """
        ---
        description: |
            Replace virtual tag value limits
            Required permission: EDIT_PARTNER
        tags: [virtual_tags]
        summary: Replace value limits
        """
        item = await run_task(self.controller.get_info, id)
        await self.check_permissions(
            'EDIT_PARTNER', 'organization', item['organization_id'])
        data = self._request_body()
        res = await run_task(
            self.controller.replace_value_limits, id,
            data.get('value_limits') or [])
        self.write(json.dumps(res, cls=ModelEncoder))


class VirtualTagRuleAsyncCollectionHandler(BaseAsyncCollectionHandler,
                                           BaseAuthHandler, BaseHandler):
    def _get_controller_class(self):
        return VirtualTagRuleAsyncController

    async def post(self, organization_id, **url_params):
        """
        ---
        description: |
            Create a virtual tag assignment rule
            Required permission: EDIT_PARTNER
        tags: [virtual_tag_rules]
        summary: Create virtual tag rule
        """
        await self.check_permissions(
            'EDIT_PARTNER', 'organization', organization_id)
        data = self._request_body()
        user_id = await self.check_self_auth()
        res = await run_task(
            self.controller.create_rule, user_id, organization_id, **data)
        self.set_status(201)
        self.write(json.dumps(res, cls=ModelEncoder))

    async def get(self, organization_id):
        """
        ---
        description: |
            List virtual tag rules
            Required permission: INFO_ORGANIZATION
        tags: [virtual_tag_rules]
        summary: List virtual tag rules
        """
        await self.check_permissions(
            'INFO_ORGANIZATION', 'organization', organization_id)
        virtual_tag_id = self.get_arg('virtual_tag_id', str)
        res = await run_task(
            self.controller.list, organization_id,
            virtual_tag_id=virtual_tag_id)
        self.write(json.dumps(res, cls=ModelEncoder))


class VirtualTagRuleAsyncItemHandler(BaseAsyncItemHandler, BaseAuthHandler,
                                     BaseHandler):
    def _get_controller_class(self):
        return VirtualTagRuleAsyncController

    async def get(self, id, **kwargs):
        item = await run_task(self.controller.get_info, id)
        await self.check_permissions(
            'INFO_ORGANIZATION', 'organization', item['organization_id'])
        self.write(json.dumps(item, cls=ModelEncoder))

    async def patch(self, id, **kwargs):
        item = await run_task(self.controller.get_info, id)
        await self.check_permissions(
            'EDIT_PARTNER', 'organization', item['organization_id'])
        data = self._request_body()
        res = await run_task(self.controller.edit_rule, id, **data)
        self.write(json.dumps(res, cls=ModelEncoder))

    async def delete(self, id, **kwargs):
        item = await run_task(self.controller.get_info, id)
        await self.check_permissions(
            'EDIT_PARTNER', 'organization', item['organization_id'])
        await run_task(self.controller.delete, id)
        self.set_status(204)


class VirtualTagRulesApplyAsyncHandler(BaseAsyncItemHandler, BaseAuthHandler,
                                       BaseHandler):
    def _get_controller_class(self):
        return VirtualTagApplyAsyncController

    async def post(self, organization_id, **url_params):
        """
        ---
        description: |
            Re-apply virtual tags for organization resources
            Required permission: EDIT_PARTNER
        tags: [virtual_tags]
        summary: Re-apply virtual tags
        """
        await self.check_permissions(
            'EDIT_PARTNER', 'organization', organization_id)
        data = self._request_body() or {}
        changed_only = data.get('changed_only', False)
        if changed_only is not None:
            check_bool_attribute('changed_only', changed_only)
        virtual_tag_id = data.get('virtual_tag_id')
        if virtual_tag_id is not None:
            check_string_attribute('virtual_tag_id', virtual_tag_id)
        res = await run_task(
            self.controller.reapply_organization, organization_id,
            quarter=data.get('quarter'), changed_only=bool(changed_only),
            virtual_tag_id=virtual_tag_id)
        self.write(json.dumps(res, cls=ModelEncoder))

    async def get(self, organization_id, **url_params):
        """
        ---
        description: |
            Virtual tag re-apply progress for a quarter
            Required permission: INFO_ORGANIZATION
        tags: [virtual_tags]
        summary: Virtual tag re-apply progress
        """
        await self.check_permissions(
            'INFO_ORGANIZATION', 'organization', organization_id)
        res = await run_task(
            self.controller.get_apply_progress, organization_id,
            quarter=self.get_arg('quarter', str))
        self.write(json.dumps(res, cls=ModelEncoder))
