import { useEffect, useMemo, useState } from "react";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { FormattedMessage } from "react-intl";
import { useDispatch } from "react-redux";
import { copyVirtualTags } from "api";
import { COPY_VIRTUAL_TAGS } from "api/restapi/actionTypes";
import Button from "components/Button";
import ButtonLoader from "components/ButtonLoader";
import Selector, { Item, ItemContent } from "components/Selector";
import { useApiState } from "hooks/useApiState";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import useVirtualTagQuarterOptions from "hooks/useVirtualTagQuarterOptions";
import { isError } from "utils/api";
import { previousQuarter } from "utils/costPeriod";

const CopyVirtualTagsContainer = ({ sourceQuarter, onCancel }) => {
  const dispatch = useDispatch();
  const { organizationId } = useOrganizationInfo();
  const { isLoading } = useApiState(COPY_VIRTUAL_TAGS);
  const { quarterOptions } = useVirtualTagQuarterOptions(sourceQuarter);
  const targetOptions = useMemo(
    () => quarterOptions.filter((quarter) => quarter !== sourceQuarter),
    [quarterOptions, sourceQuarter]
  );
  const [targetQuarter, setTargetQuarter] = useState("");
  const isSameQuarter = !targetQuarter || targetQuarter === sourceQuarter;

  useEffect(() => {
    if (!targetOptions.length) {
      return;
    }
    if (targetOptions.includes(targetQuarter)) {
      return;
    }
    const preferred = previousQuarter(sourceQuarter);
    setTargetQuarter(targetOptions.includes(preferred) ? preferred : targetOptions[0]);
  }, [sourceQuarter, targetOptions, targetQuarter]);

  const onSubmit = () =>
    dispatch((_, getState) => {
      dispatch(
        copyVirtualTags(organizationId, {
          source_quarter: sourceQuarter,
          target_quarter: targetQuarter,
        })
      ).then(() => {
        if (!isError(COPY_VIRTUAL_TAGS, getState())) {
          onCancel();
        }
      });
    });

  return (
    <Box>
      <Typography>
        <FormattedMessage
          id="copyVirtualTagsToQuarterDescription"
          values={{ source: sourceQuarter, target: targetQuarter || "—" }}
        />
      </Typography>
      <Box mt={2}>
        <Selector
          id="target-quarter-selector"
          labelMessageId="targetQuarter"
          value={targetQuarter}
          onChange={setTargetQuarter}
          fullWidth
        >
          {targetOptions.map((quarter) => (
            <Item key={quarter} value={quarter}>
              <ItemContent>{quarter}</ItemContent>
            </Item>
          ))}
        </Selector>
      </Box>
      <Box display="flex" justifyContent="flex-end" mt={2} gap={1}>
        <Button messageId="cancel" onClick={onCancel} />
        <ButtonLoader
          messageId="copyVirtualTagsToQuarter"
          variant="contained"
          color="primary"
          onClick={onSubmit}
          isLoading={isLoading}
          disabled={isSameQuarter}
        />
      </Box>
    </Box>
  );
};

export default CopyVirtualTagsContainer;
