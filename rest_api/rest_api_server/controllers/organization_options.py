import json
import logging

from tools.optscale_exceptions.common_exc import (
    WrongArgumentsException, NotFoundException, ForbiddenException)
from rest_api.rest_api_server.controllers.base import BaseController
from rest_api.rest_api_server.controllers.base_async import BaseAsyncControllerWrapper
from rest_api.rest_api_server.exceptions import Err
from rest_api.rest_api_server.controllers.sso_oidc import (
    SSO_OIDC_OPTION_NAME, merge_sso_oidc, parse_sso_oidc_value,
    redact_sso_oidc, validate_sso_oidc)
from rest_api.rest_api_server.models.models import OrganizationOption, Organization

LOG = logging.getLogger(__name__)


class OrganizationOptionsController(BaseController):
    def _get_model_type(self):
        return OrganizationOption

    def check_org(self, org_id):
        org = self.session.query(Organization).filter(
            Organization.id == org_id,
            Organization.deleted.is_(False)
        ).one_or_none()
        if not org:
            raise NotFoundException(
                Err.OE0002, [Organization.__name__, org_id])

    def _maybe_redact_value(self, option_name, value, include_secrets=False):
        if include_secrets or option_name != SSO_OIDC_OPTION_NAME:
            return value
        return json.dumps(redact_sso_oidc(parse_sso_oidc_value(value)))

    def _prepare_sso_oidc_value(self, existing_raw, data):
        existing = parse_sso_oidc_value(existing_raw)
        merged = merge_sso_oidc(existing, parse_sso_oidc_value(data))
        validate_sso_oidc(merged)
        return json.dumps(merged)

    def get_by_name(self, org_id, option_name, include_secrets=False):
        self.check_org(org_id)
        options = super().list(organization_id=org_id, name=option_name)
        if len(options) > 1:
            raise WrongArgumentsException(Err.OE0177, [])
        elif len(options) == 0:
            return '{}'
        else:
            return self._maybe_redact_value(
                option_name, options[0].value, include_secrets)

    def list(self, org_id, with_values=False, include_secrets=False):
        self.check_org(org_id)
        base_list = super().list(organization_id=org_id)
        result = [
            {
                'name': obj.name,
                'value': self._maybe_redact_value(
                    obj.name, obj.value, include_secrets)
            } if with_values else obj.name for obj in base_list
        ]
        return result

    def patch(self, org_id, option_name, data, is_secret=False):
        self.check_org(org_id)
        options = super().list(organization_id=org_id, name=option_name)
        if option_name == SSO_OIDC_OPTION_NAME:
            existing = options[0].value if len(options) == 1 else '{}'
            data = self._prepare_sso_oidc_value(existing, data)
        if len(options) > 1:
            raise WrongArgumentsException(Err.OE0177, [])
        elif len(options) == 0:
            res = super().create(organization_id=org_id, name=option_name,
                                 value=data)
            return self._maybe_redact_value(
                option_name, res.value, include_secrets=is_secret)
        else:
            option = json.loads(options[0].value)
            locked_by_user_id = option.get("locked_by")
            if locked_by_user_id:
                if not is_secret:
                    if self.token:
                        cur_user_id = self.get_user_id()
                        if cur_user_id != locked_by_user_id:
                            raise ForbiddenException(Err.OE0234, [])
                    else:
                        raise ForbiddenException(Err.OE0234, [])
            res = super().update(options[0].id, value=data)
            return self._maybe_redact_value(
                option_name, res.value, include_secrets=is_secret)

    def delete(self, org_id, option_name):
        self.check_org(org_id)
        options = super().list(organization_id=org_id, name=option_name)
        if len(options) > 1:
            raise WrongArgumentsException(Err.OE0177, [])
        elif len(options) == 0:
            raise NotFoundException(
                Err.OE0002, [OrganizationOption.__name__, option_name])
        else:
            super().delete(options[0].id)


class OrganizationOptionsAsyncController(BaseAsyncControllerWrapper):
    def _get_controller_class(self):
        return OrganizationOptionsController
