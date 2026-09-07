import { useEffect, useMemo } from "react";
import { Alert, Box, Stack, Typography } from "@mui/material";
import { FormProvider, useForm, useWatch } from "react-hook-form";
import { FormattedMessage } from "react-intl";
import CopyTextField from "components/CopyTextField";
import FormButtonsWrapper from "components/FormButtonsWrapper";
import { PasswordInput, Switch, TextInput } from "components/forms/common/fields";
import SubmitButtonLoader from "components/SubmitButtonLoader";
import OrganizationOptionsService from "services/OrganizationOptionsService";
import { SPACING_2 } from "utils/layouts";
import { getOidcLoginUrl, getOidcRedirectUri } from "utils/oidc";

const SSO_OIDC_OPTION = "sso_oidc";

type FormValues = {
  enabled: boolean;
  issuer: string;
  clientId: string;
  clientSecret: string;
  authorizationEndpoint: string;
  loginOnly: boolean;
};

const defaultValues: FormValues = {
  enabled: false,
  issuer: "",
  clientId: "",
  clientSecret: "",
  authorizationEndpoint: "",
  loginOnly: false,
};

const valuesFromOption = (value: Record<string, unknown> = {}): FormValues => ({
  enabled: Boolean(value.enabled),
  issuer: String(value.issuer || ""),
  clientId: String(value.client_id || ""),
  clientSecret: "",
  authorizationEndpoint: String(value.authorization_endpoint || ""),
  loginOnly: Boolean(value.login_only),
});

const SsoOidcSettings = () => {
  const { useGetOption, useUpdateOption } = OrganizationOptionsService();
  const { isGetOrganizationOptionLoading, value, getOption } = useGetOption();
  const { isUpdateOrganizationOptionLoading, updateOption } = useUpdateOption();

  const methods = useForm<FormValues>({ defaultValues });
  const { handleSubmit, control, reset } = methods;
  const enabled = useWatch({ control, name: "enabled" });

  useEffect(() => {
    getOption(SSO_OIDC_OPTION);
    // Load the current organization SSO option once when this tab mounts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!isGetOrganizationOptionLoading) {
      reset(valuesFromOption(value));
    }
  }, [isGetOrganizationOptionLoading, reset, value]);

  const redirectUri = useMemo(() => getOidcRedirectUri(), []);
  const loginUrl = useMemo(() => getOidcLoginUrl(), []);
  const hasClientSecret = Boolean(value?.has_client_secret);

  const onSubmit = (formData: FormValues) => {
    updateOption(
      SSO_OIDC_OPTION,
      {
        enabled: formData.enabled,
        issuer: formData.issuer.trim(),
        client_id: formData.clientId.trim(),
        client_secret: formData.clientSecret,
        authorization_endpoint: formData.authorizationEndpoint.trim(),
        login_only: formData.loginOnly,
      },
      () => getOption(SSO_OIDC_OPTION)
    );
  };

  return (
    <Stack spacing={SPACING_2} sx={{ maxWidth: 720 }}>
      <Typography variant="h6">
        <FormattedMessage id="oidcSetupTitle" />
      </Typography>
      <Typography>
        <FormattedMessage id="oidcSetupIntro" />
      </Typography>
      <Box component="ol" sx={{ pl: 3, my: 0 }}>
        {["oidcSetupStep1", "oidcSetupStep2", "oidcSetupStep3", "oidcSetupStep4", "oidcSetupStep5", "oidcSetupStep6"].map(
          (id) => (
            <Typography key={id} component="li" gutterBottom>
              <FormattedMessage id={id} />
            </Typography>
          )
        )}
      </Box>
      <Typography variant="subtitle2">
        <FormattedMessage id="oidcRedirectUri" />
      </Typography>
      <CopyTextField textToDisplay={redirectUri} />
      <Typography variant="subtitle2">
        <FormattedMessage id="oidcLoginUrl" />
      </Typography>
      <CopyTextField textToDisplay={loginUrl} />
      <Alert severity="info">
        <FormattedMessage id="oidcAutoJoinHint" />
      </Alert>
      <FormProvider {...methods}>
        <form onSubmit={handleSubmit(onSubmit)} noValidate>
          <Switch
            name="enabled"
            label={<FormattedMessage id="ssoEnabled" />}
            isLoading={isGetOrganizationOptionLoading}
          />
          <TextInput
            name="issuer"
            label={<FormattedMessage id="oidcIssuer" />}
            required={enabled}
            isLoading={isGetOrganizationOptionLoading}
            placeholder="https://<subdomain>.onelogin.com/oidc/2"
          />
          <TextInput
            name="clientId"
            label={<FormattedMessage id="clientId" />}
            required={enabled}
            isLoading={isGetOrganizationOptionLoading}
          />
          <PasswordInput
            name="clientSecret"
            label={<FormattedMessage id="clientSecret" />}
            required={enabled && !hasClientSecret}
            isLoading={isGetOrganizationOptionLoading}
          />
          <Typography variant="caption" color="textSecondary" display="block" sx={{ mb: 2 }}>
            <FormattedMessage id="oidcClientSecretKeepHint" />
          </Typography>
          <TextInput
            name="authorizationEndpoint"
            label={<FormattedMessage id="oidcAuthorizationEndpoint" />}
            isLoading={isGetOrganizationOptionLoading}
          />
          <Switch
            name="loginOnly"
            label={<FormattedMessage id="oidcLoginOnly" />}
            isLoading={isGetOrganizationOptionLoading}
          />
          <FormButtonsWrapper>
            <SubmitButtonLoader
              messageId="save"
              isLoading={isUpdateOrganizationOptionLoading}
              dataTestId="btn_save_sso_oidc"
            />
          </FormButtonsWrapper>
        </form>
      </FormProvider>
    </Stack>
  );
};

export default SsoOidcSettings;
