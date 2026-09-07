import {
  POOL_TYPE_ASSET_POOL,
  POOL_TYPE_BUDGET,
  POOL_TYPE_BUSINESS_UNIT,
  POOL_TYPE_CICD,
  POOL_TYPE_MLAI,
  POOL_TYPE_PROJECT,
  POOL_TYPE_TEAM,
  POOL_TYPES,
} from "utils/constants";

export const isCostOverLimit = ({ limit, cost }) => limit > 0 && limit < cost;

export const isForecastOverLimit = ({ limit, forecast }) => limit > 0 && limit < forecast;

// A condition when a pool is considered limited
export const hasLimit = (limit?: number) => limit !== 0;

export const POOL_TYPE_GROUP_ID_PREFIX = "__pool_type_group__:";

// Prefer human-managed types before auto-created budgets (e.g. Snowflake accounts).
export const POOL_TYPE_DISPLAY_ORDER = Object.freeze([
  POOL_TYPE_BUSINESS_UNIT,
  POOL_TYPE_TEAM,
  POOL_TYPE_PROJECT,
  POOL_TYPE_CICD,
  POOL_TYPE_MLAI,
  POOL_TYPE_ASSET_POOL,
  POOL_TYPE_BUDGET,
]);

export const getPoolTypeGroupId = (parentId: string, purpose: string) => `${POOL_TYPE_GROUP_ID_PREFIX}${parentId}:${purpose}`;

export const isPoolTypeGroup = (pool?: { isPoolTypeGroup?: boolean; id?: string }) =>
  Boolean(pool?.isPoolTypeGroup) || (typeof pool?.id === "string" && pool.id.startsWith(POOL_TYPE_GROUP_ID_PREFIX));

export const getPoolTypeMessageId = (purpose: string) => POOL_TYPES[purpose] || purpose;

export const buildPoolTypeGroupRows = (parentId: string, children: Array<Record<string, unknown>> = []) => {
  if (!children.length) {
    return [];
  }

  const byPurpose = new Map<string, Array<Record<string, unknown>>>();

  children.forEach((child) => {
    const purpose = (child.purpose as string) || POOL_TYPE_BUDGET;
    const group = byPurpose.get(purpose) || [];
    group.push(child);
    byPurpose.set(purpose, group);
  });

  return [...byPurpose.keys()]
    .sort((left, right) => {
      const leftIndex = POOL_TYPE_DISPLAY_ORDER.indexOf(left);
      const rightIndex = POOL_TYPE_DISPLAY_ORDER.indexOf(right);
      return (
        (leftIndex === -1 ? Number.MAX_SAFE_INTEGER : leftIndex) - (rightIndex === -1 ? Number.MAX_SAFE_INTEGER : rightIndex)
      );
    })
    .map((purpose) => {
      const pools = byPurpose.get(purpose) || [];
      const cost = pools.reduce((sum, pool) => sum + (Number(pool.cost) || 0), 0);
      const forecast = pools.reduce((sum, pool) => sum + (Number(pool.forecast) || 0), 0);

      return {
        id: getPoolTypeGroupId(parentId, purpose),
        isPoolTypeGroup: true,
        purpose,
        parent_id: parentId,
        name: getPoolTypeMessageId(purpose),
        cost,
        forecast,
        limit: 0,
        default_owner_name: "",
        childrenCount: pools.length,
      };
    });
};
