import { useMemo, useState } from "react";
import AddOutlinedIcon from "@mui/icons-material/AddOutlined";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import Link from "@mui/material/Link";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import { Box, TextField, Typography } from "@mui/material";
import { FormattedMessage } from "react-intl";
import { Link as RouterLink } from "react-router-dom";
import ActionBar from "components/ActionBar";
import Button from "components/Button";
import {
  DataSourceSelectionFilter,
  QuarterSelectionFilter,
  VirtualTagNameSelectionFilter,
} from "components/FilterComponents";
import IconButton from "components/IconButton";
import PageContentDescription from "components/PageContentDescription/PageContentDescription";
import PageContentWrapper from "components/PageContentWrapper";
import QuestionMark from "components/QuestionMark";
import TabsWrapper from "components/TabsWrapper";
import VirtualTagsTable from "components/VirtualTagsTable";
import { RESOURCES } from "urls";
import { isQuarter } from "utils/costPeriod";
import { SPACING_1, SPACING_2 } from "utils/layouts";

const actionBarDefinition = {
  breadcrumbs: [
    <Link key={1} to={RESOURCES} component={RouterLink}>
      <FormattedMessage id="resources" />
    </Link>,
  ],
  title: {
    text: <FormattedMessage id="virtualTags" />,
    dataTestId: "lbl_virtual_tags",
  },
};

const VirtualTags = ({
  virtualTags,
  isLoading = false,
  filters,
  onFiltersChange,
  dataSources = [],
  quarterOptions = [],
  extraQuarters = [],
  dbQuarters = [],
  onAddQuarter,
  onRemoveQuarter,
  isSavingQuarters = false,
}) => {
  const [newQuarter, setNewQuarter] = useState("");
  const nameOptions = useMemo(() => {
    const names = new Set(virtualTags.map((tag) => tag.name).filter(Boolean));
    if (filters.names.length > 0) {
      filters.names.forEach((name) => names.add(name));
    }
    return Array.from(names).sort((left, right) => left.localeCompare(right));
  }, [filters.names, virtualTags]);

  const visibleTags = useMemo(() => {
    if (filters.names.length === 0) {
      return virtualTags;
    }
    return virtualTags.filter((tag) => filters.names.includes(tag.name));
  }, [filters.names, virtualTags]);

  const listedQuarters = useMemo(
    () => Array.from(new Set(quarterOptions.filter(Boolean))).sort(),
    [quarterOptions]
  );

  const canAddQuarter = isQuarter(newQuarter.trim().toUpperCase())
    && !quarterOptions.includes(newQuarter.trim().toUpperCase());

  const addEnteredQuarter = () => {
    if (canAddQuarter && onAddQuarter(newQuarter)) {
      setNewQuarter("");
    }
  };

  const tabs = [
    {
      title: "virtualTags",
      dataTestId: "tab_virtual_tags",
      node: (
        <>
          <Box display="flex" flexWrap="wrap" gap={SPACING_1} mb={SPACING_1} alignItems="center">
            <QuarterSelectionFilter
              dataTestId="virtual-tags-quarter-filter"
              options={quarterOptions}
              value={filters.quarter}
              onChange={(quarter) => onFiltersChange({ ...filters, quarter, names: [] })}
            />
            <VirtualTagNameSelectionFilter
              dataTestId="virtual-tags-name-filter"
              options={nameOptions}
              values={filters.names}
              onChange={(names) => onFiltersChange({ ...filters, names })}
            />
            <DataSourceSelectionFilter
              dataTestId="virtual-tags-data-source-filter"
              dataSources={dataSources}
              values={filters.cloud_account_ids}
              onChange={(cloudAccountIds) =>
                onFiltersChange({ ...filters, cloud_account_ids: cloudAccountIds, names: [] })
              }
            />
          </Box>
          <VirtualTagsTable virtualTags={visibleTags} isLoading={isLoading} quarter={filters.quarter} />
          <PageContentDescription
            position="bottom"
            alertProps={{
              messageId: "virtualTagsDescription",
              messageDataTestId: "p_virtual_tags_list",
            }}
          />
        </>
      ),
    },
    {
      title: "virtualTagQuarters",
      dataTestId: "tab_virtual_tag_quarters",
      node: (
        <Box display="flex" flexDirection="column" gap={SPACING_2} sx={{ maxWidth: 560 }}>
          <Box
            sx={{
              display: "flex",
              gap: SPACING_1,
              alignItems: "center",
              "& .MuiOutlinedInput-root": {
                height: 40,
                boxSizing: "border-box",
              },
              "& .MuiInputAdornment-root": {
                height: "auto",
                maxHeight: 40,
                margin: 0,
              },
            }}
          >
            <TextField
              size="small"
              label={<FormattedMessage id="quarter" />}
              value={newQuarter}
              onChange={(event) => setNewQuarter(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  addEnteredQuarter();
                }
              }}
              placeholder="2026Q4"
              sx={{ minWidth: 160, flex: 1 }}
              InputLabelProps={{ shrink: true }}
              InputProps={{
                endAdornment: (
                  <QuestionMark
                    messageId="quarterFormatHint"
                    dataTestId="qmark_quarter_format"
                    fontSize="small"
                    withLeftMargin={false}
                  />
                ),
              }}
            />
            <Button
              dashedBorder
              startIcon={<AddOutlinedIcon fontSize="small" />}
              messageId="addQuarter"
              dataTestId="btn_add_quarter"
              disabled={!canAddQuarter || isSavingQuarters}
              onClick={addEnteredQuarter}
              sx={{
                height: 40,
                minHeight: 40,
                maxHeight: 40,
                py: 0,
                flexShrink: 0,
                boxSizing: "border-box",
                "& .MuiButton-startIcon": {
                  marginLeft: 0,
                  marginRight: 0.5,
                },
              }}
            />
          </Box>
          <Box
            sx={{
              border: 1,
              borderColor: "divider",
              borderRadius: 1,
              maxHeight: 360,
              overflowY: "auto",
            }}
          >
            {listedQuarters.length ? (
              <List disablePadding>
                {listedQuarters.map((quarter) => (
                  <ListItem
                    key={quarter}
                    divider
                    secondaryAction={
                      extraQuarters.includes(quarter) && !dbQuarters.includes(quarter) ? (
                        <IconButton
                          color="error"
                          icon={<DeleteOutlinedIcon />}
                          onClick={() => onRemoveQuarter(quarter)}
                          disabled={isSavingQuarters}
                          tooltip={{
                            show: true,
                            messageId: "delete",
                          }}
                          dataTestId={`btn_delete_quarter_${quarter}`}
                        />
                      ) : null
                    }
                  >
                    <ListItemText primary={quarter} />
                  </ListItem>
                ))}
              </List>
            ) : (
              <Typography color="text.secondary" sx={{ px: 2, py: 1.5 }}>
                <FormattedMessage id="noDataToDisplay" />
              </Typography>
            )}
          </Box>
        </Box>
      ),
    },
  ];

  return (
    <>
      <ActionBar data={actionBarDefinition} />
      <PageContentWrapper>
        <TabsWrapper
          tabsProps={{
            tabs,
            defaultTab: "virtualTags",
            name: "virtual-tags",
            queryTabName: "vtListTab",
          }}
        />
      </PageContentWrapper>
    </>
  );
};

export default VirtualTags;
