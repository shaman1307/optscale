import { useMemo, useState } from "react";
import AddOutlinedIcon from "@mui/icons-material/AddOutlined";
import ErrorOutlineOutlinedIcon from "@mui/icons-material/ErrorOutlineOutlined";
import WarningAmberOutlinedIcon from "@mui/icons-material/WarningAmberOutlined";
import { Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import { FormattedMessage } from "react-intl";
import { useNavigate } from "react-router-dom";
import CaptionedCell from "components/CaptionedCell";
import Circle from "components/Circle";
import CloudLabel from "components/CloudLabel";
import CloudType from "components/CloudType";
import FormattedMoney from "components/FormattedMoney";
import IconLabel from "components/IconLabel";
import Table from "components/Table";
import Expander from "components/Table/components/Expander";
import Pagination from "components/Table/components/Pagination";
import TableLoader from "components/TableLoader";
import Tooltip from "components/Tooltip";
import { useAwsSyntheticTenantName } from "hooks/useAwsSyntheticTenantName";
import { intl } from "translations/react-intl-config";
import { CLOUD_ACCOUNT_CONNECT } from "urls";
import { getColorScale } from "utils/charts";
import { FORMATTED_MONEY_TYPES } from "utils/constants";
import {
  BILLING_IMPORT_STATUS,
  buildCloudAccountsTableData,
  getBillingImportStatus,
} from "utils/dataSources";
import { formatUTC } from "utils/datetime";
import useStyles from "./CloudAccountsTable.styles";

const ROOT_PAGE_SIZE = 50;
const CHILD_PAGE_SIZE = 50;
const HEALTH_ROW_ALPHA = 0.12;

const NameCell = ({
  row: {
    original: {
      id,
      name,
      type,
      details: { cost, duplicate_groups: duplicateGroups, cost_mismatch: costMismatch } = {},
      last_import_at: lastImportAt,
      last_import_attempt_at: lastImportAttemptAt,
      last_import_attempt_error: lastImportAttemptError,
      allChildrenCount,
      childrenDiscoveryError,
    },
    index,
  },
  colorScale,
}) => {
  const { classes } = useStyles();
  const importStatus = getBillingImportStatus({
    timestamp: lastImportAt,
    attemptTimestamp: lastImportAttemptAt,
    error: lastImportAttemptError,
  });

  const captions = [];
  if (importStatus === BILLING_IMPORT_STATUS.ERROR) {
    captions.push({
      key: "import_failed",
      node: (
        <Typography component="div" variant="caption">
          <Tooltip title={lastImportAttemptError}>
            <span>
              <IconLabel
                icon={<ErrorOutlineOutlinedIcon fontSize="inherit" color="error" />}
                label={<FormattedMessage id="billingImportFailed" />}
              />
            </span>
          </Tooltip>
        </Typography>
      ),
    });
  }
  if (childrenDiscoveryError) {
    captions.push({
      key: "discovery_failed",
      node: (
        <Typography component="div" variant="caption">
          <Tooltip title={childrenDiscoveryError}>
            <span>
              <IconLabel
                icon={<ErrorOutlineOutlinedIcon fontSize="inherit" color="error" />}
                label={<FormattedMessage id="resourceDiscoveryFailed" />}
              />
            </span>
          </Tooltip>
        </Typography>
      ),
    });
  }
  if ((duplicateGroups ?? 0) > 0) {
    captions.push({
      key: "duplicate_groups",
      node: (
        <Typography component="div" variant="caption" color="warning.main">
          <IconLabel
            icon={<WarningAmberOutlinedIcon fontSize="inherit" color="warning" />}
            label={<FormattedMessage id="duplicateGroupsDetected" />}
          />
        </Typography>
      ),
    });
  }
  if (costMismatch) {
    captions.push({
      key: "cost_mismatch",
      node: (
        <Typography component="div" variant="caption" color="warning.main">
          <IconLabel
            icon={<WarningAmberOutlinedIcon fontSize="inherit" color="warning" />}
            label={<FormattedMessage id="stageTargetCostMismatch" />}
          />
        </Typography>
      ),
    });
  }

  return (
    <CaptionedCell caption={captions.length ? captions : undefined}>
      <CloudLabel
        id={id}
        name={name}
        type={type}
        dataTestId={`link_cloud_${index}`}
        startAdornment={
          !allChildrenCount ? (
            <span className={classes.circleSlot}>
              {cost ? <Circle color={colorScale(id)} /> : null}
            </span>
          ) : null
        }
      />
    </CaptionedCell>
  );
};

const CloudAccountsTable = ({ cloudAccounts = [], isLoading = false }) => {
  const navigate = useNavigate();

  const theme = useTheme();

  const { classes } = useStyles();

  // Per-vendor child page index; independent from root table pagination.
  const [childPageById, setChildPageById] = useState({});
  const awsTenantName = useAwsSyntheticTenantName();

  const data = useMemo(
    () =>
      buildCloudAccountsTableData({
        cloudAccounts,
        childPageById,
        childPageSize: CHILD_PAGE_SIZE,
        awsTenantName,
      }),
    [cloudAccounts, childPageById, awsTenantName]
  );

  const columns = useMemo(() => {
    const colorScale = getColorScale(theme.palette.chart);
    return [
      {
        header: intl.formatMessage({ id: "name" }),
        accessorKey: "name",
        style: {
          minWidth: 240,
        },
        cell: (cellData) => {
          const {
            row,
            row: {
              original: { id, childPageCount, childPageIndex },
            },
          } = cellData;
          const showChildPagination = row.getIsExpanded() && childPageCount > 1;

          return (
            <div className={classes.nameCellWrapper}>
              <div className={classes.nameContent}>
                <Expander row={row} />
                <div className={classes.nameLabel}>
                  <NameCell {...cellData} colorScale={colorScale} />
                </div>
              </div>
              {showChildPagination ? (
                <div
                  className={classes.childPagination}
                  onClick={(event) => event.stopPropagation()}
                  onKeyDown={(event) => event.stopPropagation()}
                  role="presentation"
                >
                  <Pagination
                    size="small"
                    position="left"
                    count={childPageCount}
                    page={childPageIndex + 1}
                    paginationHandler={(pageIndex) => {
                      setChildPageById((prev) => ({ ...prev, [id]: pageIndex }));
                    }}
                  />
                </div>
              ) : null}
            </div>
          );
        },
      },
      {
        header: intl.formatMessage({ id: "type" }),
        accessorKey: "type",
        cell: ({ cell }) => <CloudType type={cell.getValue()} />,
      },
      {
        header: intl.formatMessage({ id: "billingDataPeriod" }),
        id: "details.billing_period",
        enableSorting: false,
        accessorFn: (originalRow) => {
          const start = originalRow.details?.billing_period_start;
          const end = originalRow.details?.billing_period_end;
          if (!start || !end) {
            return null;
          }
          return `${start}:${end}`;
        },
        cell: ({ row: { original } }) => {
          const start = original.details?.billing_period_start;
          const end = original.details?.billing_period_end;
          if (!start || !end) {
            return "—";
          }
          return (
            <FormattedMessage
              id="fromTo"
              values={{
                from: formatUTC(start, "MMM d, yyyy"),
                to: formatUTC(end, "MMM d, yyyy"),
              }}
            />
          );
        },
      },
      {
        header: intl.formatMessage({ id: "resourcesChargedThisMonth" }),
        id: "details.resources",
        accessorFn: (originalRow) => originalRow.details?.resources,
        emptyValue: "0",
      },
      {
        header: intl.formatMessage({ id: "totalResources" }),
        id: "details.total_resources",
        accessorFn: (originalRow) => originalRow.details?.total_resources,
        emptyValue: "0",
      },
      {
        header: intl.formatMessage({ id: "expensesUpToDateThisMonth" }),
        id: "details.cost",
        accessorFn: (originalRow) => originalRow.details?.cost,
        cell: ({ cell }) => <FormattedMoney type={FORMATTED_MONEY_TYPES.COMMON} value={cell.getValue()} />,
        defaultSort: "desc",
      },
      {
        header: intl.formatMessage({ id: "totalExpenses" }),
        id: "details.total_cost",
        accessorFn: (originalRow) => originalRow.details?.total_cost,
        cell: ({ cell }) => <FormattedMoney type={FORMATTED_MONEY_TYPES.COMMON} value={cell.getValue()} />,
      },
      {
        header: intl.formatMessage({ id: "expensesForecastThisMonth" }),
        id: "details.forecast",
        accessorFn: (originalRow) => originalRow.details?.forecast,
        cell: ({ cell }) => <FormattedMoney type={FORMATTED_MONEY_TYPES.COMMON} value={cell.getValue()} />,
      },
    ];
  }, [theme.palette.chart, classes.nameCellWrapper, classes.nameContent, classes.nameLabel, classes.childPagination]);

  const actionBarDefinition = {
    items: [
      {
        key: "bu-add",
        dataTestId: "btn_add",
        icon: <AddOutlinedIcon fontSize="small" />,
        messageId: "add",
        color: "success",
        variant: "contained",
        type: "button",
        action: () => navigate(CLOUD_ACCOUNT_CONNECT),
        requiredActions: ["MANAGE_CLOUD_CREDENTIALS"],
      },
    ],
  };

  return isLoading ? (
    <TableLoader columnsCounter={columns.length} showHeader />
  ) : (
    <Table
      dataTestIds={{
        container: "table_accs",
      }}
      data={data}
      columns={columns}
      getRowId={(row) => row.id}
      localization={{
        emptyMessageId: "noDataSources",
      }}
      pageSize={ROOT_PAGE_SIZE}
      withExpanded
      getRowStyle={(rowData) =>
        rowData.hasHealthIssue
          ? {
              backgroundColor: alpha(theme.palette.warning.main, HEALTH_ROW_ALPHA),
            }
          : {}
      }
      actionBar={{
        show: true,
        definition: actionBarDefinition,
      }}
    />
  );
};

export default CloudAccountsTable;
