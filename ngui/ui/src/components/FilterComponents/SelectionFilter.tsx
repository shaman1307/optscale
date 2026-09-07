import React, { useId, useMemo, useState } from "react";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import SearchIcon from "@mui/icons-material/Search";
import {
  Box,
  Checkbox,
  Collapse,
  FormControlLabel,
  FormGroup,
  IconButton,
  InputAdornment,
  Popover,
  Radio,
  TextField,
  Typography,
  Divider,
  type SxProps,
  type Theme,
} from "@mui/material";
import Button from "@mui/material/Button";
import { FormattedMessage, useIntl } from "react-intl";
import { useDebouncedValue } from "hooks/useDebouncedValue";
import { isEmptyArray } from "utils/arrays";
import { isPoolTypeGroup } from "utils/pools";
import {
  collectSelectableTreeValues,
  flattenSelectionTree,
  groupItemsByKey,
  getOnlySubpoolValues,
  toggleGroupSelection,
  type SelectionTreeNode,
} from "./selectionFilterGroups";

type SelectionStateButtonProps = {
  appliedItems: string[];
  onClick: (event: React.MouseEvent<HTMLButtonElement>) => void;
  id: string;
  label: React.ReactNode;
  icon?: React.ReactNode;
  selectionLabel: () => React.ReactNode;
  sx?: SxProps<Theme>;
  error?: boolean;
};

type Value = string;

type FilterItem = {
  value: Value;
  parent_id?: string | null;
  selectable?: boolean;
  isPoolTypeGroup?: boolean;
  [key: string]: unknown;
};

type FilterSettings = {
  [key: string]: boolean;
};

type AppliedFilter = {
  values: Value[];
  settings?: FilterSettings;
};

type FiltersProps<T extends FilterItem> = {
  items: T[];
  label: React.ReactNode;
  buttonIcon?: React.ReactNode;
  renderItem: (item: T) => React.ReactNode;
  renderSelectedItem: (item: T) => React.ReactNode;
  searchPredicate: (item: T, searchQuery: string) => boolean;
  searchPlaceholder?: string;
  onChange?: (selectedItems: AppliedFilter) => void;
  appliedItems: AppliedFilter;
  settings?: {
    name: string;
    label: React.ReactNode;
  }[];
  /** When set, items are shown in collapsible groups. */
  groupBy?: (item: T) => string;
  /** Label/icon content for a group header (checkbox + collapse are added by the filter). */
  renderGroupHeader?: (groupKey: string, items: T[]) => React.ReactNode;
  /** Optional stable group key order. */
  sortGroups?: (a: string, b: string) => number;
  /** When true, grouped lists start collapsed (missing key = collapsed). */
  defaultGroupsCollapsed?: boolean;
  /**
   * Pre-built hierarchy (Pools-like tree). When set, groupBy is ignored and
   * items are shown as an expandable tree.
   */
  treeNodes?: SelectionTreeNode<T>[];
  /** When true, tree branches start collapsed (missing key = collapsed). */
  defaultTreeCollapsed?: boolean;
  /** Wider popover for deep trees (e.g. pools). */
  popoverWidth?: number | string;
  /** When true, show a loading state instead of an empty filter list. */
  isLoading?: boolean;
  /** Called when the filter popover opens. */
  onOpen?: () => void;
  /** Extra styles for the trigger button (e.g. match outlined Selector height). */
  buttonSx?: SxProps<Theme>;
  /** When true, only one value can be selected (radio instead of checkboxes). */
  single?: boolean;
  /** Error styling for the trigger button (form fields). */
  error?: boolean;
};

const SelectionStateButton = ({
  appliedItems,
  onClick,
  id,
  label,
  icon,
  selectionLabel,
  sx,
  error = false,
}: SelectionStateButtonProps) => (
  <Button
    aria-describedby={id}
    variant={appliedItems.length > 0 ? "contained" : "outlined"}
    onClick={onClick}
    color={error ? "error" : "primary"}
    startIcon={icon}
    sx={sx}
  >
    {label} ({selectionLabel()})
  </Button>
);

const isSelectableItem = <T extends FilterItem>(item: T) => item.selectable !== false && !isPoolTypeGroup(item);

const SelectionFilter = <T extends FilterItem>({
  items,
  label,
  buttonIcon,
  renderItem,
  renderSelectedItem,
  searchPredicate,
  onChange,
  appliedItems,
  settings = [],
  groupBy,
  renderGroupHeader,
  sortGroups,
  defaultGroupsCollapsed = false,
  treeNodes,
  defaultTreeCollapsed = true,
  popoverWidth = 300,
  isLoading = false,
  onOpen: onOpenProp,
  buttonSx,
  single = false,
  error = false,
}: FiltersProps<T>) => {
  const intl = useIntl();

  const [anchorEl, setAnchorEl] = useState<HTMLButtonElement | null>(null);
  const [selectedValues, setSelectedValues] = useState<Value[]>([]);
  const [selectedSettings, setSelectedSettings] = useState<FilterSettings>({});
  // true = collapsed; missing key means expanded unless default*Collapsed.
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const [collapsedBranches, setCollapsedBranches] = useState<Record<string, boolean>>({});

  const [searchQuery, setSearchQuery] = useState("");
  const debouncedSearchQuery = useDebouncedValue(searchQuery, { delay: 300 });

  const treeItems = useMemo(() => (treeNodes ? flattenSelectionTree(treeNodes) : items), [treeNodes, items]);

  // Create a map of available values for quick lookup (selectable pools only for chips).
  const valueItemsMap = useMemo(
    () => new Map(treeItems.filter(isSelectableItem).map((item) => [item.value, item])),
    [treeItems]
  );

  // Combine available and unavailable items
  const allItems = useMemo(() => {
    const unavailableItems = appliedItems.values
      .filter((value) => !valueItemsMap.has(value))
      .map((value) => ({ value, name: value, selectable: true }) as T);
    return [...treeItems, ...unavailableItems];
  }, [treeItems, appliedItems.values, valueItemsMap]);

  const popoverId = useId();

  const onOpen = (event: React.MouseEvent<HTMLButtonElement>) => {
    setAnchorEl(event.currentTarget);
    setSelectedValues(appliedItems.values);
    setSelectedSettings(appliedItems.settings || {});
    setSearchQuery("");
    setCollapsedGroups({});
    setCollapsedBranches({});
    onOpenProp?.();
  };

  const handleCancel = () => {
    setAnchorEl(null);
    setSelectedValues(appliedItems.values);
    setSelectedSettings(appliedItems.settings || {});
    setSearchQuery("");
  };

  const handleApply = () => {
    setAnchorEl(null);
    setSearchQuery("");

    onChange?.({
      values: selectedValues,
      settings: selectedSettings,
    });
  };

  const handleSettingChange = (name: string) => (event: React.ChangeEvent<HTMLInputElement>) => {
    const checked = event.target.checked;
    setSelectedSettings((prev) => ({
      ...prev,
      [name]: checked,
    }));
    // "Only sub-pools": drop org/project pools from the current draft selection.
    if (name === "onlySubpools" && checked) {
      const subpoolValues = new Set(getOnlySubpoolValues(allItems));
      setSelectedValues((prev) => prev.filter((value) => subpoolValues.has(value)));
    }
  };

  const handleItemToggle = (newValue: Value) => {
    setSelectedValues((prev) => {
      if (single) {
        return [newValue];
      }
      if (prev.includes(newValue)) {
        return prev.filter((value) => value !== newValue);
      }
      return [...prev, newValue];
    });
  };

  const open = Boolean(anchorEl);
  const id = open ? `filter-popover-${popoverId}` : `filter-popover-${popoverId}-closed`;

  const hasChanges =
    JSON.stringify(selectedValues) !== JSON.stringify(appliedItems.values) ||
    JSON.stringify(selectedSettings) !== JSON.stringify(appliedItems.settings || {});

  const filteredItems = allItems
    .filter((item) => {
      const isUnavailable = !valueItemsMap.has(item.value) && isSelectableItem(item);

      if (isUnavailable) {
        return item.value.toString().toLocaleLowerCase().includes(debouncedSearchQuery.toLocaleLowerCase());
      }
      if (!isSelectableItem(item) && !treeNodes) {
        return searchPredicate(item as T, debouncedSearchQuery);
      }
      if (!isSelectableItem(item)) {
        // Type-group rows stay when any descendant matches; handled in tree filter.
        return true;
      }
      return searchPredicate(item as T, debouncedSearchQuery);
    })
    .sort((a, b) => {
      const isAUnavailable = !valueItemsMap.has(a.value) && isSelectableItem(a);
      const isBUnavailable = !valueItemsMap.has(b.value) && isSelectableItem(b);
      if (isAUnavailable && !isBUnavailable) {
        return -1;
      }
      if (!isAUnavailable && isBUnavailable) {
        return 1;
      }
      return 0;
    });

  const filteredValues = filteredItems.filter(isSelectableItem).map((item) => item.value);

  const selectableFilteredValues = selectedSettings.onlySubpools ? getOnlySubpoolValues(filteredItems as T[]) : filteredValues;

  const allFilteredSelected =
    selectableFilteredValues.length > 0 && selectableFilteredValues.every((value) => selectedValues.includes(value));

  const someFilteredSelected =
    selectableFilteredValues.length > 0 &&
    selectableFilteredValues.some((value) => selectedValues.includes(value)) &&
    !allFilteredSelected;

  const handleSelectAll = (event: React.ChangeEvent<HTMLInputElement>) => {
    if (event.target.checked) {
      setSelectedValues(selectableFilteredValues);
    } else {
      setSelectedValues([]);
    }
  };

  const getSelectAllLabel = () => {
    if (debouncedSearchQuery) {
      return <FormattedMessage id="filteredResults" values={{ count: selectableFilteredValues.length }} />;
    }
    if (isEmptyArray(selectedValues)) {
      return <FormattedMessage id="selectAll" />;
    }
    return (
      <FormattedMessage
        id="countOfTotalSelected"
        values={{
          count: selectedValues.length,
          total: selectedSettings.onlySubpools ? selectableFilteredValues.length : filteredValues.length,
        }}
      />
    );
  };

  const groupedFilteredItems = useMemo(() => {
    if (!groupBy || treeNodes) {
      return null;
    }
    return groupItemsByKey(filteredItems as T[], groupBy, sortGroups);
  }, [filteredItems, groupBy, sortGroups, treeNodes]);

  const isGroupCollapsed = (groupKey: string) =>
    defaultGroupsCollapsed ? collapsedGroups[groupKey] !== false : Boolean(collapsedGroups[groupKey]);

  const toggleGroupCollapsed = (groupKey: string) => {
    setCollapsedGroups((prev) => {
      const currentlyCollapsed = defaultGroupsCollapsed ? prev[groupKey] !== false : Boolean(prev[groupKey]);
      return {
        ...prev,
        [groupKey]: !currentlyCollapsed,
      };
    });
  };

  const isBranchCollapsed = (branchKey: string) =>
    defaultTreeCollapsed ? collapsedBranches[branchKey] !== false : Boolean(collapsedBranches[branchKey]);

  const toggleBranchCollapsed = (branchKey: string) => {
    setCollapsedBranches((prev) => {
      const currentlyCollapsed = defaultTreeCollapsed ? prev[branchKey] !== false : Boolean(prev[branchKey]);
      return {
        ...prev,
        [branchKey]: !currentlyCollapsed,
      };
    });
  };

  const handleGroupSelect = (groupItems: T[]) => (event: React.ChangeEvent<HTMLInputElement>) => {
    const groupValues = selectedSettings.onlySubpools
      ? getOnlySubpoolValues(groupItems)
      : groupItems.filter(isSelectableItem).map((item) => item.value);
    setSelectedValues((prev) => toggleGroupSelection(prev, groupValues, event.target.checked));
  };

  const handleTreeNodeSelect = (node: SelectionTreeNode<T>) => (event: React.ChangeEvent<HTMLInputElement>) => {
    const nodeValues = collectSelectableTreeValues(node);
    const itemsForFilter = nodeValues.map((value) => valueItemsMap.get(value)).filter(Boolean) as T[];
    const finalValues = selectedSettings.onlySubpools ? getOnlySubpoolValues(itemsForFilter) : nodeValues;
    setSelectedValues((prev) => toggleGroupSelection(prev, finalValues, event.target.checked));
  };

  const renderItemRow = (item: T | FilterItem, indentPx = 16) => {
    const selectable = isSelectableItem(item as T);
    return (
      <FormControlLabel
        key={item.value}
        control={
          selectable ? (
            single ? (
              <Radio checked={selectedValues.some((i) => i === item.value)} onChange={() => handleItemToggle(item.value)} />
            ) : (
              <Checkbox checked={selectedValues.some((i) => i === item.value)} onChange={() => handleItemToggle(item.value)} />
            )
          ) : (
            <Box sx={{ width: 42 }} />
          )
        }
        label={valueItemsMap.has(item.value) || !selectable ? renderItem(item as T) : String(item.value)}
        sx={{
          px: 1,
          pl: `${indentPx}px`,
          width: "100%",
          overflowWrap: "anywhere",
          mr: 0,
        }}
      />
    );
  };

  const filterTreeNodes = (nodes: SelectionTreeNode<T>[], query: string): SelectionTreeNode<T>[] => {
    if (!query) {
      return nodes;
    }
    return nodes
      .map((node) => {
        const children = filterTreeNodes(node.children, query);
        const selfMatch = isSelectableItem(node.item) && searchPredicate(node.item, query);
        if (selfMatch || children.length > 0) {
          return { ...node, children };
        }
        return null;
      })
      .filter(Boolean) as SelectionTreeNode<T>[];
  };

  const renderTreeNode = (node: SelectionTreeNode<T>, depth: number) => {
    const searching = Boolean(debouncedSearchQuery);
    const hasChildren = node.children.length > 0;
    const branchKey = node.item.value;
    const collapsed = searching ? false : isBranchCollapsed(branchKey);
    const selectableValues = (() => {
      const values = collectSelectableTreeValues(node);
      if (!selectedSettings.onlySubpools) {
        return values;
      }
      const itemsForFilter = values.map((value) => valueItemsMap.get(value)).filter(Boolean) as T[];
      return getOnlySubpoolValues(itemsForFilter);
    })();
    const allSelected = selectableValues.length > 0 && selectableValues.every((value) => selectedValues.includes(value));
    const someSelected =
      selectableValues.length > 0 && selectableValues.some((value) => selectedValues.includes(value)) && !allSelected;
    const indentPx = 8 + depth * 16;
    const selectable = isSelectableItem(node.item);

    return (
      <Box key={node.item.value}>
        <Box sx={{ display: "flex", alignItems: "center", pr: 0.5 }}>
          {hasChildren ? (
            <IconButton
              size="small"
              aria-label={collapsed ? `Expand ${node.item.value}` : `Collapse ${node.item.value}`}
              onClick={() => toggleBranchCollapsed(branchKey)}
              sx={{ ml: `${Math.max(indentPx - 8, 0)}px` }}
            >
              {collapsed ? <ExpandMoreIcon fontSize="small" /> : <ExpandLessIcon fontSize="small" />}
            </IconButton>
          ) : (
            <Box sx={{ width: 34, ml: `${Math.max(indentPx - 8, 0)}px` }} />
          )}
          {selectable || hasChildren ? (
            <Checkbox
              size="small"
              checked={allSelected}
              indeterminate={someSelected}
              disabled={selectableValues.length === 0 && !selectable}
              onChange={hasChildren ? handleTreeNodeSelect(node) : () => handleItemToggle(node.item.value)}
            />
          ) : (
            <Box sx={{ width: 42 }} />
          )}
          <Box sx={{ flex: 1, minWidth: 0, py: 0.25 }}>{renderItem(node.item)}</Box>
        </Box>
        {hasChildren ? (
          <Collapse in={!collapsed} timeout="auto" unmountOnExit>
            <Box>{node.children.map((child) => renderTreeNode(child, depth + 1))}</Box>
          </Collapse>
        ) : null}
      </Box>
    );
  };

  const renderTreeItems = () => {
    if (!treeNodes) {
      return null;
    }
    const visibleNodes = filterTreeNodes(treeNodes, debouncedSearchQuery);
    const unavailableItems = filteredItems.filter((item) => !valueItemsMap.has(item.value) && isSelectableItem(item));

    return (
      <Box sx={{ maxHeight: "360px", overflowY: "auto", overflowX: "hidden" }}>
        {unavailableItems.map((item) => renderItemRow(item, 16))}
        {visibleNodes.map((node) => renderTreeNode(node, 0))}
        {visibleNodes.length === 0 && unavailableItems.length === 0 ? (
          <Typography variant="body2" textAlign="center" sx={{ py: 1, px: 1, overflowWrap: "anywhere" }}>
            <FormattedMessage id="noMatches" values={{ searchQuery: debouncedSearchQuery }} />
          </Typography>
        ) : null}
      </Box>
    );
  };

  const renderGroupedItems = () => {
    if (!groupedFilteredItems) {
      return null;
    }
    const searching = Boolean(debouncedSearchQuery);
    return (
      <Box sx={{ maxHeight: "300px", overflowY: "auto", overflowX: "hidden" }}>
        {groupedFilteredItems.map(({ key, items: groupItems }) => {
          const selectableGroupValues = selectedSettings.onlySubpools
            ? getOnlySubpoolValues(groupItems)
            : groupItems.filter(isSelectableItem).map((item) => item.value);
          const allSelected =
            selectableGroupValues.length > 0 && selectableGroupValues.every((value) => selectedValues.includes(value));
          const someSelected =
            selectableGroupValues.length > 0 &&
            selectableGroupValues.some((value) => selectedValues.includes(value)) &&
            !allSelected;
          const isCollapsed = searching ? false : isGroupCollapsed(key);

          return (
            <Box key={key}>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  px: 1,
                  py: 0.25,
                  bgcolor: "action.hover",
                }}
              >
                {single ? null : (
                  <Checkbox
                    size="small"
                    checked={allSelected}
                    indeterminate={someSelected}
                    disabled={selectableGroupValues.length === 0}
                    onChange={handleGroupSelect(groupItems)}
                    inputProps={{ "aria-label": `Select all ${key}` }}
                  />
                )}
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    flex: 1,
                    minWidth: 0,
                    cursor: "pointer",
                    gap: 0.5,
                  }}
                  onClick={() => toggleGroupCollapsed(key)}
                >
                  <Box sx={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 0.75 }}>
                    {renderGroupHeader ? (
                      renderGroupHeader(key, groupItems)
                    ) : (
                      <Typography variant="subtitle2">
                        {key} ({groupItems.length})
                      </Typography>
                    )}
                    {!renderGroupHeader ? null : (
                      <Typography variant="caption" color="text.secondary" component="span">
                        ({groupItems.length})
                      </Typography>
                    )}
                  </Box>
                  <IconButton
                    size="small"
                    aria-label={isCollapsed ? `Expand ${key}` : `Collapse ${key}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      toggleGroupCollapsed(key);
                    }}
                  >
                    {isCollapsed ? <ExpandMoreIcon fontSize="small" /> : <ExpandLessIcon fontSize="small" />}
                  </IconButton>
                </Box>
              </Box>
              <Collapse in={!isCollapsed} timeout="auto" unmountOnExit>
                <Box sx={{ py: 0.25 }}>{groupItems.map((item) => renderItemRow(item, 32))}</Box>
              </Collapse>
            </Box>
          );
        })}
      </Box>
    );
  };

  const renderFlatItems = () => (
    <>
      {filteredItems.length > 0 && (
        <Box sx={{ maxHeight: "300px", overflowY: "auto", overflowX: "hidden" }}>
          <Box sx={{ py: 0.5 }}>{filteredItems.map((item) => renderItemRow(item))}</Box>
        </Box>
      )}
    </>
  );

  const renderList = () => {
    if (treeNodes) {
      return renderTreeItems();
    }
    if (groupBy) {
      return renderGroupedItems();
    }
    return renderFlatItems();
  };

  const renderFilterContent = () => {
    if (isLoading) {
      return (
        <Typography variant="body2" textAlign="center" sx={{ py: 2, px: 1, wordBreak: "break-all" }}>
          <FormattedMessage id="loadingFilterValues" />
        </Typography>
      );
    }

    if (isEmptyArray(allItems)) {
      return (
        <Typography variant="body2" textAlign="center" sx={{ py: 2, px: 1, wordBreak: "break-all" }}>
          <FormattedMessage id="noFiltersAvailable" />
        </Typography>
      );
    }

    return (
      <>
        {isEmptyArray(settings) ? null : (
          <>
            <Typography variant="subtitle2" color="primary" sx={{ pt: 2, px: 2, fontWeight: "bold" }}>
              <FormattedMessage id="settings" />
            </Typography>
            <Box sx={{ px: 2, pb: 0 }}>
              {settings.map((setting) => (
                <FormControlLabel
                  key={setting.name}
                  control={
                    <Checkbox checked={selectedSettings[setting.name] || false} onChange={handleSettingChange(setting.name)} />
                  }
                  label={setting.label}
                />
              ))}
            </Box>
            <Divider />
            <Typography variant="subtitle2" color="primary" sx={{ pt: 2, px: 2, fontWeight: "bold" }}>
              <FormattedMessage id="filters" />
            </Typography>
          </>
        )}
        <Box sx={{ px: 2, borderBottom: 1, borderColor: "divider", pb: 1, pt: 1 }}>
          <TextField
            size="small"
            placeholder={intl.formatMessage({ id: "search" })}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            fullWidth
            variant="standard"
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon fontSize="small" />
                </InputAdornment>
              ),
            }}
          />
        </Box>
        <FormGroup>
          {selectableFilteredValues.length > 0 && !single && (
            <>
              <FormControlLabel
                control={
                  <Checkbox checked={allFilteredSelected} indeterminate={someFilteredSelected} onChange={handleSelectAll} />
                }
                label={getSelectAllLabel()}
                sx={{ px: 2, py: 0 }}
              />
              <Divider />
            </>
          )}
          {renderList()}
          {!treeNodes && filteredItems.length === 0 && (
            <Typography variant="body2" textAlign="center" sx={{ py: 1, px: 1, overflowWrap: "anywhere" }}>
              <FormattedMessage id="noMatches" values={{ searchQuery: debouncedSearchQuery }} />
            </Typography>
          )}
        </FormGroup>
      </>
    );
  };

  return (
    <>
      <SelectionStateButton
        appliedItems={appliedItems.values}
        onClick={onOpen}
        id={id}
        label={label}
        selectionLabel={() => {
          if (isEmptyArray(appliedItems.values)) {
            return <FormattedMessage id="any" />;
          }

          const firstValue = appliedItems.values[0];
          const renderedFirstItem = valueItemsMap.has(firstValue)
            ? renderSelectedItem(valueItemsMap.get(firstValue) as T)
            : String(firstValue);

          if (appliedItems.values.length === 1) {
            return renderedFirstItem;
          }

          return (
            <>
              {renderedFirstItem}, +{appliedItems.values.length - 1}
            </>
          );
        }}
        icon={buttonIcon}
        sx={buttonSx}
        error={error}
      />
      <Popover
        id={id}
        open={open}
        anchorEl={anchorEl}
        onClose={handleCancel}
        anchorOrigin={{
          vertical: "bottom",
          horizontal: "left",
        }}
      >
        <Box sx={{ display: "flex", flexDirection: "column", width: popoverWidth }}>
          {renderFilterContent()}
          <Box sx={{ display: "flex", justifyContent: "flex-end", gap: 1, px: 2, py: 1, borderTop: 1, borderColor: "divider" }}>
            <Button onClick={handleCancel} variant="outlined">
              <FormattedMessage id="cancel" />
            </Button>
            <Button onClick={handleApply} variant="contained" disabled={!hasChanges || isLoading || (single && isEmptyArray(selectedValues))} color="primary">
              <FormattedMessage id="apply" />
            </Button>
          </Box>
        </Box>
      </Popover>
    </>
  );
};

export default SelectionFilter;
