import LoginOutlinedIcon from "@mui/icons-material/LoginOutlined";
import ButtonLoader from "components/ButtonLoader";
import { isOidcConfigured, startOidcLogin, type OidcRuntimeConfig } from "utils/oidc";

type OidcSignInButtonProps = {
  isLoading?: boolean;
  disabled?: boolean;
  oidcConfig?: OidcRuntimeConfig;
};

const OidcSignInButton = ({ isLoading = false, disabled = false, oidcConfig }: OidcSignInButtonProps) => {
  const configured = oidcConfig ? oidcConfig.enabled : isOidcConfigured();

  return (
    <ButtonLoader
      variant="outlined"
      messageId="signInWithSso"
      size="medium"
      onClick={() => startOidcLogin(oidcConfig)}
      startIcon={<LoginOutlinedIcon />}
      isLoading={isLoading}
      disabled={disabled || !configured}
      fullWidth
      tooltip={{
        show: !configured,
        messageId: "signInWithSsoIsNotConfigured",
      }}
    />
  );
};

export default OidcSignInButton;
