import { useEffect, useMemo, useState } from "react";
import { useDispatch } from "react-redux";
import { useNavigate, useParams, Link as RouterLink } from "react-router-dom";
import AddOutlinedIcon from "@mui/icons-material/AddOutlined";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import RepeatOutlinedIcon from "@mui/icons-material/RepeatOutlined";
import WarningAmberOutlinedIcon from "@mui/icons-material/WarningAmberOutlined";
import { Box, Link, TextField, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import { FormattedMessage } from "react-intl";
import { getVirtualTag, getVirtualTagRules, getVirtualTags, updateVirtualTagValueLimits, RESTAPI } from "api";
import { GET_VIRTUAL_TAG, GET_VIRTUAL_TAG_RULES, GET_VIRTUAL_TAGS, UPDATE_VIRTUAL_TAG_VALUE_LIMITS } from "api/restapi/actionTypes";
import ActionBar from "components/ActionBar";
import ButtonLoader from "components/ButtonLoader";
import { DataSourceSelectionFilter, QuarterSelectionFilter, VirtualTagValueSelectionFilter } from "components/FilterComponents";
import IconLabel from "components/IconLabel";
import PageContentWrapper from "components/PageContentWrapper";
import TabsWrapper from "components/TabsWrapper";
import { DeleteVirtualTagRuleModal, ReapplyVirtualTagsModal } from "components/SideModalManager/SideModals";
import Table from "components/Table";
import TableCellActions from "components/TableCellActions";
import TableLoader from "components/TableLoader";
import TextWithDataTestId from "components/TextWithDataTestId";
import Tooltip from "components/Tooltip";
import { useAllDataSources } from "hooks/coreData/useAllDataSources";
import { useApiData } from "hooks/useApiData";
import { useApiState } from "hooks/useApiState";
import { useOpenSideModal } from "hooks/useOpenSideModal";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import useVirtualTagQuarterOptions from "hooks/useVirtualTagQuarterOptions";
import { VIRTUAL_TAGS, getCreateVirtualTagRuleUrl, getEditVirtualTagRuleUrl, getEditVirtualTagUrl, getVirtualTagUrl } from "urls";
import { CLOUD_IS } from "utils/constants";
import { SPACING_1, SPACING_2 } from "utils/layouts";

const VirtualTagContainer = () => {
  const { virtualTagId } = useParams();
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const openSideModal = useOpenSideModal();
  const { organizationId } = useOrganizationInfo();
  const theme = useTheme();
  const dataSources = useAllDataSources();
  const [limits, setLimits] = useState({});
  const [ruleFilters, setRuleFilters] = useState({
    values: [] as string[],
    cloud_account_ids: [] as string[],
  });

  const {
    apiData: { virtualTag = {} },
  } = useApiData(GET_VIRTUAL_TAG, { virtualTag: {} });
  const {
    apiData: { virtualTagRules = [] },
  } = useApiData(GET_VIRTUAL_TAG_RULES, { virtualTagRules: [] });
  const {
    apiData: { virtualTags = [] },
  } = useApiData(GET_VIRTUAL_TAGS, { virtualTags: [], quarters: [] });
  const { quarterOptions } = useVirtualTagQuarterOptions(virtualTag.quarter);

  const { isLoading: isTagLoading, shouldInvoke: shouldLoadTag } = useApiState(GET_VIRTUAL_TAG, virtualTagId);
  const { isLoading: isRulesLoading, shouldInvoke: shouldLoadRules } = useApiState(GET_VIRTUAL_TAG_RULES, {
    organizationId,
    virtualTagId,
  });
  const { isLoading: isSavingLimits } = useApiState(UPDATE_VIRTUAL_TAG_VALUE_LIMITS);

  useEffect(() => {
    if (shouldLoadTag) {
      dispatch(getVirtualTag(virtualTagId));
    }
  }, [dispatch, shouldLoadTag, virtualTagId]);

  useEffect(() => {
    if (shouldLoadRules) {
      dispatch(getVirtualTagRules(organizationId, virtualTagId));
    }
  }, [dispatch, organizationId, shouldLoadRules, virtualTagId]);

  const stats = virtualTag.stats || [];

  useEffect(() => {
    setLimits(Object.fromEntries((virtualTag.stats || []).map((item) => [item.value, item.limit ?? ""])));
  }, [virtualTag.stats]);

  const saveLimits = () => {
    dispatch(
      updateVirtualTagValueLimits(virtualTagId, {
        value_limits: Object.entries(limits)
          .filter(([, limit]) => limit !== "" && limit != null)
          .map(([value, limit]) => ({ value, limit: Number(limit) })),
      })
    );
  };

  const switchQuarter = (quarter) => {
    if (!quarter || quarter === virtualTag.quarter) {
      return;
    }
    dispatch((_, getState) => {
      dispatch(getVirtualTags(organizationId, { quarter })).then(() => {
        const siblings = getState()?.[RESTAPI]?.[GET_VIRTUAL_TAGS]?.virtualTags || [];
        const sibling = siblings.find((item) => item.key === virtualTag.key);
        if (sibling?.id) {
          navigate(getVirtualTagUrl(sibling.id));
        }
      });
    });
  };

  const ruleValueOptions = useMemo(() => {
    const values = new Set();
    virtualTagRules.forEach((rule) => {
      (rule.branches || []).forEach((branch) => {
        (branch.allocations || []).forEach((item) => {
          if (item.value) {
            values.add(item.value);
          }
        });
      });
    });
    if (ruleFilters.values.length > 0) {
      ruleFilters.values.forEach((value) => values.add(value));
    }
    return Array.from(values).sort((left, right) => String(left).localeCompare(String(right)));
  }, [ruleFilters.values, virtualTagRules]);

  const filteredRules = useMemo(
    () =>
      virtualTagRules.filter((rule) => {
        const values = (rule.branches || []).flatMap((branch) =>
          (branch.allocations || []).map((item) => item.value)
        );
        if (
          ruleFilters.values.length > 0 &&
          !ruleFilters.values.some((value) => values.includes(value))
        ) {
          return false;
        }
        const cloudIds = (rule.branches || []).flatMap((branch) =>
          (branch.conditions || [])
            .filter((condition) => condition.type === CLOUD_IS)
            .map((condition) => condition.meta_info)
        );
        if (
          ruleFilters.cloud_account_ids.length > 0 &&
          !cloudIds.some((id) => ruleFilters.cloud_account_ids.includes(id))
        ) {
          return false;
        }
        return true;
      }),
    [ruleFilters, virtualTagRules]
  );

  const siblingMissing =
    virtualTag.key &&
    virtualTags.length > 0 &&
    virtualTags[0]?.quarter &&
    virtualTags[0].quarter !== virtualTag.quarter &&
    !virtualTags.some((item) => item.key === virtualTag.key);

  const rulesById = useMemo(
    () => Object.fromEntries(virtualTagRules.map((rule) => [rule.id, rule])),
    [virtualTagRules]
  );

  const ruleColumns = useMemo(
    () => [
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_name">
            <FormattedMessage id="name" />
          </TextWithDataTestId>
        ),
        accessorKey: "name",
        cell: ({ row: { original } }) => {
          const overlapIds = original.overlap_rule_ids || [];
          if (!overlapIds.length) {
            return original.name;
          }
          const names = overlapIds.map((id) => rulesById[id]?.name || id).join(", ");
          return (
            <IconLabel
              icon={
                <Tooltip title={<FormattedMessage id="virtualTagRuleOverlap" values={{ names }} />}>
                  <WarningAmberOutlinedIcon fontSize="small" color="warning" />
                </Tooltip>
              }
              label={original.name}
            />
          );
        },
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_active">
            <FormattedMessage id="active" />
          </TextWithDataTestId>
        ),
        accessorKey: "active",
        cell: ({ row: { original } }) => <FormattedMessage id={original.active ? "yes" : "no"} />,
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_actions">
            <FormattedMessage id="actions" />
          </TextWithDataTestId>
        ),
        id: "actions",
        cell: ({ row: { original } }) => (
          <TableCellActions
            items={[
              {
                key: "edit",
                messageId: "edit",
                icon: <EditOutlinedIcon />,
                action: () => navigate(getEditVirtualTagRuleUrl(virtualTagId, original.id)),
              },
              {
                key: "delete",
                messageId: "delete",
                icon: <DeleteOutlinedIcon />,
                color: "error",
                action: () =>
                  openSideModal(DeleteVirtualTagRuleModal, {
                    ruleId: original.id,
                    ruleName: original.name,
                  }),
              },
            ]}
          />
        ),
      },
    ],
    [navigate, openSideModal, rulesById, virtualTagId]
  );

  const tabs = [
    {
      title: "valueLimits",
      dataTestId: "tab_value_limits",
      node: (
        <Box display="flex" flexDirection="column" gap={SPACING_2}>
          <Table
            data={stats}
            columns={[
              {
                header: <FormattedMessage id="value" />,
                accessorKey: "value",
              },
              {
                header: <FormattedMessage id="count" />,
                accessorKey: "resource_count",
              },
              {
                header: <FormattedMessage id="currentMonthSpend" />,
                accessorKey: "cost",
                cell: ({ row: { original } }) => (
                  <Typography color={original.over_limit ? "error" : "inherit"}>
                    {Math.round(original.cost ?? 0)}
                  </Typography>
                ),
              },
              {
                header: <FormattedMessage id="forecast" />,
                accessorKey: "forecast",
                cell: ({ row: { original } }) => Math.round(original.forecast ?? 0),
              },
              {
                header: <FormattedMessage id="limit" />,
                accessorKey: "limit",
                cell: ({ row: { original } }) => (
                  <TextField
                    size="small"
                    type="number"
                    value={limits[original.value] ?? ""}
                    onChange={(event) =>
                      setLimits((current) => ({
                        ...current,
                        [original.value]: event.target.value,
                      }))
                    }
                  />
                ),
              },
            ]}
            localization={{ emptyMessageId: "noVirtualTags" }}
          />
          {stats.length > 0 && (
            <Box>
              <ButtonLoader
                messageId="saveValueLimits"
                variant="contained"
                color="primary"
                onClick={saveLimits}
                isLoading={isSavingLimits}
              />
            </Box>
          )}
        </Box>
      ),
    },
    {
      title: "virtualTagRules",
      dataTestId: "tab_virtual_tag_rules",
      node: (
        <Box display="flex" flexDirection="column" gap={SPACING_2}>
          <Box display="flex" flexWrap="wrap" gap={SPACING_1} alignItems="center">
            <VirtualTagValueSelectionFilter
              dataTestId="virtual-tag-rules-value-filter"
              options={ruleValueOptions}
              values={ruleFilters.values}
              onChange={(values) => setRuleFilters((current) => ({ ...current, values }))}
            />
            <DataSourceSelectionFilter
              dataTestId="virtual-tag-rules-data-source-filter"
              dataSources={dataSources}
              values={ruleFilters.cloud_account_ids}
              onChange={(cloudAccountIds) =>
                setRuleFilters((current) => ({ ...current, cloud_account_ids: cloudAccountIds }))
              }
            />
          </Box>
          {isRulesLoading ? (
            <TableLoader columnsCounter={3} />
          ) : (
            <Table
              data={filteredRules}
              columns={ruleColumns}
              getRowStyle={(row) =>
                (row.overlap_rule_ids || []).length
                  ? { backgroundColor: alpha(theme.palette.warning.main, 0.12) }
                  : {}
              }
              actionBar={{
                show: true,
                definition: {
                  items: [
                    {
                      key: "add",
                      icon: <AddOutlinedIcon fontSize="small" />,
                      messageId: "addVirtualTagRule",
                      type: "button",
                      variant: "contained",
                      link: getCreateVirtualTagRuleUrl(virtualTagId),
                      requiredActions: ["EDIT_PARTNER"],
                    },
                  ],
                },
              }}
              localization={{ emptyMessageId: "noVirtualTagRules" }}
            />
          )}
        </Box>
      ),
    },
  ];

  return (
    <>
      <ActionBar
        data={{
          breadcrumbs: [
            <Link key={1} to={VIRTUAL_TAGS} component={RouterLink}>
              <FormattedMessage id="virtualTags" />
            </Link>,
          ],
          title: {
            text: virtualTag.name || virtualTag.key || "",
          },
          items: [
            {
              key: "reapply",
              icon: <RepeatOutlinedIcon fontSize="small" />,
              messageId: "reapplyVirtualTag",
              type: "button",
              action: () =>
                openSideModal(ReapplyVirtualTagsModal, {
                  quarter: virtualTag.quarter,
                  virtualTagId,
                  virtualTagName: virtualTag.name || virtualTag.key,
                }),
              requiredActions: ["EDIT_PARTNER"],
              dataTestId: "btn_reapply_virtual_tag",
            },
            {
              key: "edit",
              icon: <EditOutlinedIcon fontSize="small" />,
              messageId: "edit",
              type: "button",
              link: getEditVirtualTagUrl(virtualTagId),
              requiredActions: ["EDIT_PARTNER"],
            },
          ],
        }}
      />
      <PageContentWrapper>
        {isTagLoading ? (
          <TableLoader columnsCounter={3} />
        ) : (
          <Box display="flex" flexDirection="column" gap={SPACING_2}>
            <Typography>
              <FormattedMessage id="virtualTagKey" />: <strong>{virtualTag.key}</strong>
            </Typography>
            <Typography>
              <FormattedMessage id="virtualTagMode" />:{" "}
              <FormattedMessage id={virtualTag.mode === "extract" ? "virtualTagModeExtract" : "virtualTagModeAssignment"} />
            </Typography>
            <QuarterSelectionFilter
              dataTestId="virtual-tag-quarter-filter"
              options={quarterOptions}
              value={virtualTag.quarter || ""}
              onChange={switchQuarter}
            />
            {siblingMissing && (
              <Typography color="text.secondary">
                <FormattedMessage id="noVirtualTagInQuarter" />
              </Typography>
            )}
            <TabsWrapper
              tabsProps={{
                tabs,
                defaultTab: "valueLimits",
                name: "virtual-tag",
                keepTabContentMounted: true,
              }}
            />
          </Box>
        )}
      </PageContentWrapper>
    </>
  );
};

export default VirtualTagContainer;
