import { useEffect, useState } from "react";
import { useIntl } from "react-intl";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import { AWS_SYNTHETIC_TENANT_NAME_CHANGED, readAwsSyntheticTenantName } from "utils/dataSources/awsSyntheticTenantName";

export const useAwsSyntheticTenantName = () => {
  const intl = useIntl();
  const { organizationId } = useOrganizationInfo();
  const defaultName = intl.formatMessage({ id: "aws" });

  const [name, setName] = useState(() => {
    if (!organizationId) {
      return defaultName;
    }
    return readAwsSyntheticTenantName(organizationId) || defaultName;
  });

  useEffect(() => {
    if (!organizationId) {
      setName(defaultName);
      return;
    }
    setName(readAwsSyntheticTenantName(organizationId) || defaultName);
  }, [organizationId, defaultName]);

  useEffect(() => {
    const onNameChanged = (event: Event) => {
      const { organizationId: changedOrgId, name: changedName } = (event as CustomEvent).detail ?? {};
      if (changedOrgId === organizationId && changedName) {
        setName(changedName);
      }
    };

    window.addEventListener(AWS_SYNTHETIC_TENANT_NAME_CHANGED, onNameChanged);
    return () => window.removeEventListener(AWS_SYNTHETIC_TENANT_NAME_CHANGED, onNameChanged);
  }, [organizationId]);

  return name;
};
