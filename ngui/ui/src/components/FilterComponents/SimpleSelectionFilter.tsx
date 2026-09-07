import { type ReactNode } from "react";
import CalendarTodayOutlinedIcon from "@mui/icons-material/CalendarTodayOutlined";
import LabelOutlinedIcon from "@mui/icons-material/LabelOutlined";
import LocalOfferOutlinedIcon from "@mui/icons-material/LocalOfferOutlined";
import Box from "@mui/material/Box";
import { FormattedMessage } from "react-intl";
import SelectionFilter from "./SelectionFilter";

type SimpleItem = {
  value: string;
  name: string;
};

type SimpleSelectionFilterProps = {
  label: ReactNode;
  buttonIcon?: React.ReactNode;
  options: string[];
  values: string[];
  onChange: (values: string[]) => void;
  single?: boolean;
  dataTestId?: string;
};

const toItems = (options: string[]): SimpleItem[] =>
  options.filter(Boolean).map((value) => ({ value, name: value }));

const SimpleSelectionFilter = ({
  label,
  buttonIcon,
  options,
  values,
  onChange,
  single = false,
  dataTestId,
}: SimpleSelectionFilterProps) => (
  <Box data-test-id={dataTestId} sx={{ flex: "0 0 auto" }}>
    <SelectionFilter
      items={toItems(options)}
      label={label}
      buttonIcon={buttonIcon}
      renderItem={(item) => item.name}
      renderSelectedItem={(item) => item.name}
      searchPredicate={(item, query) => item.name.toLowerCase().includes(query.toLowerCase())}
      appliedItems={{ values }}
      onChange={(selected) => onChange(selected.values)}
      single={single}
    />
  </Box>
);

export const QuarterSelectionFilter = ({
  options,
  value,
  onChange,
  dataTestId,
}: {
  options: string[];
  value: string;
  onChange: (quarter: string) => void;
  dataTestId?: string;
}) => (
  <SimpleSelectionFilter
    dataTestId={dataTestId}
    label={<FormattedMessage id="quarter" />}
    buttonIcon={<CalendarTodayOutlinedIcon />}
    options={options}
    values={value ? [value] : []}
    onChange={(values) => {
      if (values[0]) {
        onChange(values[0]);
      }
    }}
    single
  />
);

export const VirtualTagNameSelectionFilter = ({
  options,
  values,
  onChange,
  dataTestId,
}: {
  options: string[];
  values: string[];
  onChange: (values: string[]) => void;
  dataTestId?: string;
}) => (
  <SimpleSelectionFilter
    dataTestId={dataTestId}
    label={<FormattedMessage id="virtualTagName" />}
    buttonIcon={<LabelOutlinedIcon />}
    options={options}
    values={values}
    onChange={onChange}
  />
);

export const VirtualTagValueSelectionFilter = ({
  options,
  values,
  onChange,
  dataTestId,
}: {
  options: string[];
  values: string[];
  onChange: (values: string[]) => void;
  dataTestId?: string;
}) => (
  <SimpleSelectionFilter
    dataTestId={dataTestId}
    label={<FormattedMessage id="virtualTagValue" />}
    buttonIcon={<LocalOfferOutlinedIcon />}
    options={options}
    values={values}
    onChange={onChange}
  />
);

export default SimpleSelectionFilter;
