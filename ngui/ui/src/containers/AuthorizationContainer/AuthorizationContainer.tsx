import { useEffect, useRef, useState } from "react";
import { ApolloError } from "@apollo/client";
import { Alert, Stack, Typography } from "@mui/material";
import { FormattedMessage } from "react-intl";
import { useDispatch } from "react-redux";
import { useLocation, useNavigate } from "react-router-dom";
import LoginForm from "components/forms/LoginForm";
import RegistrationForm from "components/forms/RegistrationForm";
import GoogleAuthButton from "components/GoogleAuthButton";
import Greeter from "components/Greeter";
import MicrosoftSignInButton from "components/MicrosoftSignInButton";
import OAuthSignIn from "components/OAuthSignIn";
import OidcSignInButton from "components/OidcSignInButton";
import Redirector from "components/Redirector";
import { initialize } from "containers/InitializeContainer/redux";
import { useCreateTokenMutation, useCreateUserMutation, useSignInMutation } from "graphql/__generated__/hooks/auth";
import { useGetToken } from "hooks/useGetToken";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import VerifyEmailService from "services/VerifyEmailService";
import {
  REGISTER,
  LOGIN,
  INITIALIZE,
  EMAIL_VERIFICATION,
  SHOW_POLICY_QUERY_PARAM,
  USER_EMAIL_QUERY_PARAMETER_NAME,
  NEXT_QUERY_PARAMETER_NAME,
  OPTSCALE_CAPABILITY_QUERY_PARAMETER_NAME,
} from "urls";
import { GA_EVENT_CATEGORIES, trackEvent } from "utils/analytics";
import { processGraphQLErrorData } from "utils/apollo";
import { AUTH_PROVIDERS, OPTSCALE_CAPABILITY } from "utils/constants";
import { ERROR_CODES } from "utils/errorCodes";
import { SPACING_4 } from "utils/layouts";
import macaroon from "utils/macaroons";
import { stringifySearchParams, getSearchParams } from "utils/network";
import { getOidcRedirectUri, loadOidcRuntimeConfig, OIDC_STATE_KEY, startOidcLogin, type OidcRuntimeConfig } from "utils/oidc";

const AuthorizationContainer = () => {
  const { pathname } = useLocation();
  const dispatch = useDispatch();

  const { invited: queryInvited } = getSearchParams();

  const { isDemo } = useOrganizationInfo();

  const { token } = useGetToken();

  const navigate = useNavigate();

  const isTokenExists = Boolean(token);

  const { useSendEmailVerificationCode } = VerifyEmailService();

  const { onSend: sendEmailVerificationCode, isLoading: isSendEmailVerificationCodeLoading } = useSendEmailVerificationCode();

  const [createToken, { loading: loginLoading }] = useCreateTokenMutation();

  const [createUser, { loading: registerLoading }] = useCreateUserMutation();

  const [signIn, { loading: signInLoading }] = useSignInMutation();

  const [oidcConfig, setOidcConfig] = useState<OidcRuntimeConfig | null>(null);
  const oidcConfigured = Boolean(oidcConfig?.enabled);
  const oidcLoginOnly = Boolean(oidcConfigured && oidcConfig?.loginOnly);
  const [oidcErrorId, setOidcErrorId] = useState<string | null>(null);
  const oidcHandled = useRef(false);

  useEffect(() => {
    let cancelled = false;
    loadOidcRuntimeConfig().then((config) => {
      if (!cancelled) {
        setOidcConfig(config);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleLogin = async ({ email, password }) => {
    try {
      const { data } = await createToken({ variables: { email, password } });
      const caveats = macaroon.processCaveats(macaroon.deserialize(data.token.token).getCaveats());
      dispatch(initialize({ ...data.token, caveats }));
    } catch (error) {
      if (error instanceof ApolloError) {
        const errorsData = error.graphQLErrors.map(processGraphQLErrorData);
        if (errorsData.some(({ errorCode }) => errorCode === ERROR_CODES.OA0073)) {
          navigate(`${EMAIL_VERIFICATION}?${stringifySearchParams({ email })}`);
        }
      }
    }
  };

  const handleRegister = async ({ email, password, name }: { email: string; password: string; name: string }) => {
    const { data } = await createUser({ variables: { email, password, name } });

    trackEvent({ category: GA_EVENT_CATEGORIES.USER, action: "Registered", label: "optscale" });

    if (data.user.verified) {
      const caveats = macaroon.processCaveats(macaroon.deserialize(data.user.token).getCaveats());
      return dispatch(initialize({ ...data.user, caveats }));
    }

    const { [OPTSCALE_CAPABILITY_QUERY_PARAMETER_NAME]: capability } = getSearchParams() as {
      [OPTSCALE_CAPABILITY_QUERY_PARAMETER_NAME]: string;
    };

    await sendEmailVerificationCode(email, {
      [OPTSCALE_CAPABILITY_QUERY_PARAMETER_NAME]: Object.values(OPTSCALE_CAPABILITY).includes(capability)
        ? capability
        : undefined,
    });

    return navigate(
      `${EMAIL_VERIFICATION}?${stringifySearchParams({
        email,
        [OPTSCALE_CAPABILITY_QUERY_PARAMETER_NAME]: capability,
      })}`
    );
  };

  const handleThirdPartySignIn = async ({ provider, token: thirdPartyToken, tenantId, redirectUri }) => {
    const { data } = await signIn({
      variables: { provider, token: thirdPartyToken, tenantId, redirectUri },
    });

    const caveats = macaroon.processCaveats(macaroon.deserialize(data.signIn.token).getCaveats());
    if (caveats.register) {
      trackEvent({ category: GA_EVENT_CATEGORIES.USER, action: "Registered", label: caveats.provider });
    }
    dispatch(initialize({ ...data.signIn, caveats }));
  };

  useEffect(() => {
    if (!oidcConfig || oidcHandled.current || isTokenExists) {
      return;
    }

    const params = getSearchParams() as { code?: string; state?: string; error?: string; sso?: string };
    if (params.error) {
      oidcHandled.current = true;
      setOidcErrorId("oidcSignInFailed");
      return;
    }

    if (params.code) {
      oidcHandled.current = true;
      const savedState = sessionStorage.getItem(OIDC_STATE_KEY);
      sessionStorage.removeItem(OIDC_STATE_KEY);
      if (!params.state || params.state !== savedState) {
        setOidcErrorId("oidcInvalidState");
        return;
      }
      handleThirdPartySignIn({
        provider: AUTH_PROVIDERS.OIDC,
        token: String(params.code),
        redirectUri: getOidcRedirectUri(),
      }).catch(() => setOidcErrorId("oidcSignInFailed"));
      return;
    }

    if (!oidcConfigured) {
      return;
    }

    const sso = String(params.sso || "").toLowerCase();
    const shouldStart = oidcLoginOnly || sso === "oidc" || sso === "onelogin";
    if (shouldStart && pathname === LOGIN) {
      oidcHandled.current = true;
      startOidcLogin(oidcConfig);
    }
    // Run once the public SSO config is loaded.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [oidcConfig, oidcConfigured, oidcLoginOnly, isTokenExists, pathname]);

  const isInvited = queryInvited !== undefined;

  const createForm =
    {
      [LOGIN]: () =>
        oidcLoginOnly ? null : (
          <LoginForm
            onSubmit={handleLogin}
            isLoading={loginLoading}
            disabled={registerLoading || signInLoading}
            isInvited={isInvited}
          />
        ),
      [REGISTER]: () =>
        oidcLoginOnly ? null : (
          <RegistrationForm
            onSubmit={handleRegister}
            isLoading={registerLoading}
            disabled={loginLoading || signInLoading || isSendEmailVerificationCodeLoading}
            isInvited={isInvited}
          />
        ),
    }[pathname] || (() => null);

  // TODO: get back to the force redirect
  // redirecting already authorized user from /login and /register pages
  // const shouldRedirectAuthorizedUser = !isAuthInProgress && !isRegistrationInProgress && !isDemo && isTokenExists;
  const shouldRedirectAuthorizedUser = !isDemo && isTokenExists;

  const getRedirectionPath = () => {
    const {
      [NEXT_QUERY_PARAMETER_NAME]: next,
      [OPTSCALE_CAPABILITY_QUERY_PARAMETER_NAME]: capability,
      [USER_EMAIL_QUERY_PARAMETER_NAME]: email,
      [SHOW_POLICY_QUERY_PARAM]: showPolicy,
    } = getSearchParams() as {
      [NEXT_QUERY_PARAMETER_NAME]: string;
      [OPTSCALE_CAPABILITY_QUERY_PARAMETER_NAME]: string;
      [USER_EMAIL_QUERY_PARAMETER_NAME]: string;
      [SHOW_POLICY_QUERY_PARAM]: boolean | string;
    };

    return `${INITIALIZE}?${stringifySearchParams({
      [NEXT_QUERY_PARAMETER_NAME]: next,
      [OPTSCALE_CAPABILITY_QUERY_PARAMETER_NAME]: capability,
      [USER_EMAIL_QUERY_PARAMETER_NAME]: email,
      [SHOW_POLICY_QUERY_PARAM]: showPolicy,
    })}`;
  };

  const showLocalOAuth = !oidcLoginOnly;

  return (
    <Redirector condition={oidcLoginOnly && pathname === REGISTER} to={LOGIN}>
      <Redirector condition={shouldRedirectAuthorizedUser} to={getRedirectionPath()}>
        <Greeter
          content={
            <Stack spacing={SPACING_4}>
              {oidcErrorId ? (
                <Alert severity="error">
                  <FormattedMessage id={oidcErrorId} />
                </Alert>
              ) : null}
              {oidcLoginOnly && !oidcErrorId ? (
                <Typography align="center">
                  <FormattedMessage id="oidcRedirecting" />
                </Typography>
              ) : null}
              <div>{createForm()}</div>
              {oidcConfigured ? (
                <div>
                  <OidcSignInButton
                    oidcConfig={oidcConfig}
                    isLoading={signInLoading}
                    disabled={loginLoading || registerLoading}
                  />
                </div>
              ) : null}
              {showLocalOAuth ? (
                <div>
                  <OAuthSignIn
                    googleButton={
                      <GoogleAuthButton
                        handleSignIn={handleThirdPartySignIn}
                        isLoading={signInLoading}
                        disabled={loginLoading || registerLoading}
                      />
                    }
                    microsoftButton={
                      <MicrosoftSignInButton
                        handleSignIn={handleThirdPartySignIn}
                        isLoading={signInLoading}
                        disabled={loginLoading || registerLoading}
                      />
                    }
                  />
                </div>
              ) : null}
            </Stack>
          }
        />
      </Redirector>
    </Redirector>
  );
};

export default AuthorizationContainer;
