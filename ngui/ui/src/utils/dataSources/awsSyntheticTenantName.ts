const STORAGE_KEY_PREFIX = "awsSyntheticTenantName:";

export const AWS_SYNTHETIC_TENANT_NAME_CHANGED = "awsSyntheticTenantNameChanged";

export const getAwsSyntheticTenantStorageKey = (organizationId: string) => `${STORAGE_KEY_PREFIX}${organizationId}`;

export const readAwsSyntheticTenantName = (organizationId: string): string | null => {
  try {
    return localStorage.getItem(getAwsSyntheticTenantStorageKey(organizationId));
  } catch {
    return null;
  }
};

export const writeAwsSyntheticTenantName = (organizationId: string, name: string) => {
  localStorage.setItem(getAwsSyntheticTenantStorageKey(organizationId), name);
  window.dispatchEvent(
    new CustomEvent(AWS_SYNTHETIC_TENANT_NAME_CHANGED, {
      detail: { organizationId, name },
    })
  );
};

export const clearAwsSyntheticTenantName = (organizationId: string) => {
  localStorage.removeItem(getAwsSyntheticTenantStorageKey(organizationId));
};
