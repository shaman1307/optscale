import json
import logging

from rest_api.rest_api_server.controllers.base import BaseController
from rest_api.rest_api_server.controllers.base_async import BaseAsyncControllerWrapper
from rest_api.rest_api_server.exceptions import Err
from rest_api.rest_api_server.models.models import Organization, OrganizationOption
from tools.optscale_exceptions.common_exc import WrongArgumentsException

LOG = logging.getLogger(__name__)

SSO_OIDC_OPTION_NAME = 'sso_oidc'

EMPTY_SSO_LOGIN_CONFIG = {
    'configured': False,
    'enabled': False,
    'issuer': '',
    'client_id': '',
    'authorization_endpoint': '',
    'login_only': False,
}


def parse_sso_oidc_value(raw):
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def is_sso_oidc_enabled(cfg):
    return bool(
        cfg.get('enabled') and cfg.get('issuer') and cfg.get('client_id'))


def redact_sso_oidc(cfg):
    redacted = {
        key: value for key, value in cfg.items() if key != 'client_secret'
    }
    redacted['has_client_secret'] = bool(cfg.get('client_secret'))
    return redacted


def merge_sso_oidc(existing, incoming):
    merged = dict(existing)
    merged.update(incoming)
    merged.pop('has_client_secret', None)
    if not incoming.get('client_secret'):
        merged['client_secret'] = existing.get('client_secret') or ''
    return merged


def validate_sso_oidc(cfg):
    if not is_sso_oidc_enabled(cfg) and not cfg.get('enabled'):
        return
    if cfg.get('enabled'):
        if not cfg.get('issuer'):
            raise WrongArgumentsException(Err.OE0216, ['issuer'])
        if not cfg.get('client_id'):
            raise WrongArgumentsException(Err.OE0216, ['client_id'])
        if not cfg.get('client_secret'):
            raise WrongArgumentsException(Err.OE0216, ['client_secret'])


def find_sso_oidc(session):
    options = session.query(OrganizationOption).join(
        Organization,
        Organization.id == OrganizationOption.organization_id
    ).filter(
        OrganizationOption.name == SSO_OIDC_OPTION_NAME,
        OrganizationOption.deleted.is_(False),
        Organization.deleted.is_(False),
    ).all()
    enabled = []
    others = []
    for option in options:
        cfg = parse_sso_oidc_value(option.value)
        if is_sso_oidc_enabled(cfg):
            enabled.append((option.organization_id, cfg))
        else:
            others.append((option.organization_id, cfg))
    if enabled:
        if len(enabled) > 1:
            LOG.warning(
                'Multiple enabled sso_oidc options found; using organization %s',
                enabled[0][0])
        return enabled[0]
    if others:
        return others[0]
    return None, {}


def find_enabled_sso_oidc(session):
    org_id, cfg = find_sso_oidc(session)
    if org_id and is_sso_oidc_enabled(cfg):
        return org_id, cfg
    return None, {}


def public_sso_login_config(cfg, include_secret=False):
    if not cfg:
        return dict(EMPTY_SSO_LOGIN_CONFIG)
    result = {
        'configured': True,
        'enabled': is_sso_oidc_enabled(cfg),
        'issuer': cfg.get('issuer') or '',
        'client_id': cfg.get('client_id') or '',
        'authorization_endpoint': cfg.get('authorization_endpoint') or '',
        'login_only': bool(cfg.get('login_only')),
    }
    if include_secret:
        result['client_secret'] = cfg.get('client_secret') or ''
    return result


class SsoOidcController(BaseController):
    def _get_model_type(self):
        return OrganizationOption

    def get_login_config(self, include_secret=False):
        _, cfg = find_sso_oidc(self.session)
        return public_sso_login_config(cfg, include_secret=include_secret)


class SsoOidcAsyncController(BaseAsyncControllerWrapper):
    def _get_controller_class(self):
        return SsoOidcController
