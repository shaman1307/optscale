import { LOGIN } from "urls";
import { getApiUrl } from "api/utils";
import { getEnvironmentVariable } from "./env";

export const OIDC_STATE_KEY = "optscale_oidc_state";

export type OidcRuntimeConfig = {
  configured: boolean;
  enabled: boolean;
  issuer: string;
  clientId: string;
  authorizationEndpoint: string;
  loginOnly: boolean;
};

type SsoLoginConfigResponse = {
  configured?: boolean;
  enabled?: boolean;
  issuer?: string;
  client_id?: string;
  authorization_endpoint?: string;
  login_only?: boolean;
};

export const getOidcRedirectUri = () => `${window.location.origin}${LOGIN}`;

export const getOidcLoginUrl = () => `${getOidcRedirectUri()}?sso=oidc`;

const envOidcConfig = (): OidcRuntimeConfig => {
  const issuer = getEnvironmentVariable("VITE_OIDC_ISSUER");
  const clientId = getEnvironmentVariable("VITE_OIDC_CLIENT_ID");
  return {
    configured: Boolean(clientId && issuer),
    enabled: Boolean(clientId && issuer),
    issuer,
    clientId,
    authorizationEndpoint: getEnvironmentVariable("VITE_OIDC_AUTHORIZATION_ENDPOINT"),
    loginOnly: getEnvironmentVariable("VITE_OIDC_LOGIN_ONLY") === "true",
  };
};

export const isOidcConfigured = () => envOidcConfig().enabled;

export const isOidcLoginOnly = () => envOidcConfig().loginOnly;

export const mergeOidcConfig = (api?: SsoLoginConfigResponse | null): OidcRuntimeConfig => {
  const env = envOidcConfig();
  if (api?.configured) {
    return {
      configured: true,
      enabled: Boolean(api.enabled && api.issuer && api.client_id),
      issuer: api.issuer || "",
      clientId: api.client_id || "",
      authorizationEndpoint: api.authorization_endpoint || "",
      loginOnly: Boolean(api.login_only) || env.loginOnly,
    };
  }
  return env;
};

export const loadOidcRuntimeConfig = async (): Promise<OidcRuntimeConfig> => {
  try {
    const response = await fetch(`${getApiUrl("restapi")}/sso_login_config`);
    if (response.ok) {
      return mergeOidcConfig(await response.json());
    }
  } catch {
    // Fall back to build-time environment if the public config is unavailable.
  }
  return envOidcConfig();
};

const randomString = () => {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
};

export const startOidcLogin = (config?: OidcRuntimeConfig) => {
  const resolved = config && config.enabled ? config : envOidcConfig();
  const issuer = resolved.issuer.replace(/\/$/, "");
  const authEndpoint = resolved.authorizationEndpoint || `${issuer}/auth`;
  const state = randomString();
  sessionStorage.setItem(OIDC_STATE_KEY, state);
  const params = new URLSearchParams({
    client_id: resolved.clientId,
    redirect_uri: getOidcRedirectUri(),
    response_type: "code",
    scope: "openid profile email",
    state,
  });
  window.location.assign(`${authEndpoint}?${params.toString()}`);
};
