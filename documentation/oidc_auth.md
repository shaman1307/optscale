# Configure login with OpenID Connect (OneLogin and other OIDC IdPs)

OptScale accepts an OIDC authorization code on `POST /auth/v2/signin` with `provider=oidc`. After verifying the id_token, it finds or creates a local user by email and issues an OptScale token.

Organization managers configure SSO on **User Management → SSO**. That organization is also the auto-join target: SSO users with no organization are added there as members.

The login page reads public settings from `GET /restapi/v2/sso_login_config` (no client secret). Environment variables remain a fallback when no UI config exists.

OneLogin requires HTTPS redirect URIs (except `http://localhost` for development). Register the public or internal `https://` hostname, not a raw IP over HTTP.

## OneLogin

1. In OneLogin create an **OpenId Connect (OIDC)** app (not SAML Custom Connector).

2. Set:

   - Grant: Authorization Code
   - Application type: Web
   - Scopes: `openid profile email`
   - Redirect URI: `https://<optscale-host>/login` (also shown on the SSO tab)
   - Login URL / Initiate Login URI: `https://<optscale-host>/login?sso=oidc`

3. Copy **Issuer URL**, **Client ID**, and **Client Secret** into User Management → SSO. Issuer is typically `https://<subdomain>.onelogin.com/oidc/2`. Confirm the id_token includes an `email` claim (or `preferred_username` as an email).

4. Enable SSO and save. New SSO users join that organization automatically.

## Docker Compose (optional fallback)

If the UI is not used, set these in `optscale-deploy/compose/.env` and rebuild `auth` and `ngui`:

```
OIDC_ISSUER=https://<subdomain>.onelogin.com/oidc/2
OIDC_CLIENT_ID=
OIDC_CLIENT_SECRET=
# Optional. Defaults to {OIDC_ISSUER}/auth
#OIDC_AUTHORIZATION_ENDPOINT=
# Set to true to hide email/password, Google, and Microsoft login
OIDC_LOGIN_ONLY=true
```

The auth service needs outbound HTTPS to the issuer (discovery, token, JWKS). OneLogin does not connect inbound to OptScale.

If SSO is saved as disabled in the UI, environment variables are ignored.

## Kubernetes overlay

The same env fallback can be set in `optscale-deploy/overlay/user_template.yml` under `auth` (`oidc_issuer`, `oidc_client_id`, `oidc_client_secret`) and `ngui.env` (`oidc_client_id`, `oidc_issuer`, `oidc_login_only`). Prefer the User Management SSO tab when an organization already exists.
