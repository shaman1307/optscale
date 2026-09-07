const STAGE_TARGET_COST_TOLERANCE = 1;

export const hasStageTargetCostMismatch = (localSum, targetSum) => {
  if (localSum == null || targetSum == null) {
    return false;
  }
  const local = Number(localSum);
  const target = Number(targetSum);
  if (!Number.isFinite(local) || !Number.isFinite(target)) {
    return false;
  }
  return Math.abs(local - target) > STAGE_TARGET_COST_TOLERANCE;
};

export const hasDataSourceHealthIssue = (dataSource) => {
  const details = dataSource?.details ?? {};
  return (details.duplicate_groups ?? 0) > 0 || Boolean(details.cost_mismatch);
};
