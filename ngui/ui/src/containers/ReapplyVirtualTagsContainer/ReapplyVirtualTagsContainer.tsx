import { useEffect, useState } from "react";
import Box from "@mui/material/Box";
import FormControlLabel from "@mui/material/FormControlLabel";
import LinearProgress from "@mui/material/LinearProgress";
import Typography from "@mui/material/Typography";
import { FormattedMessage } from "react-intl";
import { useDispatch } from "react-redux";
import { applyVirtualTagRules, getVirtualTagApplyProgress } from "api";
import { APPLY_VIRTUAL_TAG_RULES } from "api/restapi/actionTypes";
import Button from "components/Button";
import ButtonLoader from "components/ButtonLoader";
import Checkbox from "components/Checkbox";
import { useApiState } from "hooks/useApiState";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import { isError } from "utils/api";

const POLL_MS = 2000;

const ReapplyVirtualTagsContainer = ({ onCancel, quarter, virtualTagId, virtualTagName }) => {
  const dispatch = useDispatch();
  const { organizationId } = useOrganizationInfo();
  const { isLoading } = useApiState(APPLY_VIRTUAL_TAG_RULES);
  const [progress, setProgress] = useState(null);
  const [changedOnly, setChangedOnly] = useState(true);
  const isSingle = Boolean(virtualTagId);

  useEffect(() => {
    if (!isLoading) {
      return undefined;
    }
    let cancelled = false;
    const poll = () => {
      dispatch(getVirtualTagApplyProgress(organizationId, quarter ? { quarter } : {})).then((response) => {
        if (!cancelled && response?.data) {
          setProgress(response.data);
        }
      });
    };
    poll();
    const intervalId = window.setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [dispatch, isLoading, organizationId, quarter]);

  const onSubmit = () => {
    setProgress({ pct: 0, processed: 0, total: 0, state: "running" });
    dispatch((_, getState) => {
      dispatch(
        applyVirtualTagRules(organizationId, {
          ...(quarter ? { quarter } : {}),
          ...(virtualTagId ? { virtual_tag_id: virtualTagId } : {}),
          changed_only: changedOnly,
        })
      ).then(() => {
        if (!isError(APPLY_VIRTUAL_TAG_RULES, getState())) {
          onCancel();
        }
      });
    });
  };

  const pct = Number.isFinite(progress?.pct) ? progress.pct : null;

  return (
    <Box>
      <Typography>
        {isSingle ? (
          <FormattedMessage
            id="reapplyVirtualTagDescription"
            values={{ quarter, name: virtualTagName || virtualTagId }}
          />
        ) : (
          <FormattedMessage id="reapplyVirtualTagsDescription" values={{ quarter }} />
        )}
      </Typography>
      <FormControlLabel
        sx={{ mt: 1, ml: 0, alignItems: "center" }}
        control={
          <Checkbox
            data-test-id="checkbox_changed_only"
            size="small"
            sx={{ p: 0, mr: 1 }}
            checked={changedOnly}
            disabled={isLoading}
            onChange={() => setChangedOnly((current) => !current)}
          />
        }
        label={<FormattedMessage id="reapplyVirtualTagsChangedOnly" />}
      />
      {isLoading && (
        <Box mt={2}>
          <LinearProgress variant={pct == null ? "indeterminate" : "determinate"} value={pct ?? 0} />
          <Typography variant="body2" color="text.secondary" mt={1}>
            {pct == null ? (
              <FormattedMessage id={isSingle ? "reapplyVirtualTag" : "reapplyVirtualTags"} />
            ) : (
              <FormattedMessage
                id="reapplyVirtualTagsProgress"
                values={{
                  pct,
                  processed: progress?.processed ?? 0,
                  total: progress?.total ?? 0,
                }}
              />
            )}
          </Typography>
        </Box>
      )}
      <Box display="flex" justifyContent="flex-end" mt={2} gap={1}>
        <Button messageId="cancel" onClick={onCancel} />
        <ButtonLoader
          messageId={isSingle ? "reapplyVirtualTag" : "reapplyVirtualTags"}
          variant="contained"
          color="primary"
          onClick={onSubmit}
          isLoading={isLoading}
        />
      </Box>
    </Box>
  );
};

export default ReapplyVirtualTagsContainer;
