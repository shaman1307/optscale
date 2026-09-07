import { makeStyles } from "tss-react/mui";

const useStyles = makeStyles()((theme) => ({
  nameCellWrapper: {
    display: "flex",
    alignItems: "center",
    flexWrap: "wrap",
    gap: theme.spacing(1),
    width: "100%",
  },
  childPagination: {
    marginLeft: "auto",
    flexShrink: 0,
  },
}));

export default useStyles;
