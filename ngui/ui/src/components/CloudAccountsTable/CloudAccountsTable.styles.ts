import { makeStyles } from "tss-react/mui";

const useStyles = makeStyles()((theme) => ({
  nameCellWrapper: {
    display: "flex",
    alignItems: "center",
    flexWrap: "wrap",
    gap: theme.spacing(1),
    width: "100%",
    minWidth: 0,
  },
  // Expander + name stay on one line so short/long project names share the same indent.
  // Pagination may still wrap below on a narrow name column.
  nameContent: {
    display: "flex",
    alignItems: "center",
    flexWrap: "nowrap",
    gap: theme.spacing(1),
    minWidth: 0,
    flex: "1 1 auto",
  },
  nameLabel: {
    minWidth: 0,
  },
  circleSlot: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: "1em",
    marginRight: theme.spacing(1),
    flexShrink: 0,
  },
  childPagination: {
    marginLeft: "auto",
    flexShrink: 0,
  },
}));

export default useStyles;
