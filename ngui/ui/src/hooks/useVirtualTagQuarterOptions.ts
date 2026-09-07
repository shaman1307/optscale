import { useEffect, useMemo, useRef, useState } from "react";
import { useDispatch } from "react-redux";
import { getOrganizationOptions } from "api";
import { GET_VIRTUAL_TAGS } from "api/restapi/actionTypes";
import { useApiData } from "hooks/useApiData";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import OrganizationOptionsService from "services/OrganizationOptionsService";
import { currentQuarter, isQuarter } from "utils/costPeriod";

export const VIRTUAL_TAG_QUARTERS_OPTION = "virtual_tag_quarters";

const parseStoredQuarters = (value) => {
  if (Array.isArray(value)) {
    return value.filter(isQuarter);
  }
  if (value && Array.isArray(value.quarters)) {
    return value.quarters.filter(isQuarter);
  }
  return [];
};

const sameQuarterList = (left, right) => {
  if (left === right) {
    return true;
  }
  if (!left || !right || left.length !== right.length) {
    return false;
  }
  return left.every((item, index) => item === right[index]);
};

const useVirtualTagQuarterOptions = (selectedQuarter = "") => {
  const dispatch = useDispatch();
  const { organizationId } = useOrganizationInfo();
  const {
    apiData: { quarters: storedFromTags = [] },
  } = useApiData(GET_VIRTUAL_TAGS, { virtualTags: [], quarters: [] });
  const { useGet, useCreateOption } = OrganizationOptionsService();
  const { options = [] } = useGet(true);
  const { createOption, isCreateOrganizationOptionLoading } = useCreateOption();

  const serverExtras = useMemo(() => {
    const option = options.find((item) => item.name === VIRTUAL_TAG_QUARTERS_OPTION);
    return parseStoredQuarters(option?.value);
  }, [options]);

  // Local list so Add/Remove update the UI immediately. Ignore stale server
  // snapshots until they match the pending save.
  const [extraQuarters, setExtraQuarters] = useState(serverExtras);
  const pendingExtrasRef = useRef(null);

  useEffect(() => {
    if (pendingExtrasRef.current) {
      if (sameQuarterList(pendingExtrasRef.current, serverExtras)) {
        pendingExtrasRef.current = null;
        setExtraQuarters(serverExtras);
      }
      return;
    }
    setExtraQuarters((current) => (sameQuarterList(current, serverExtras) ? current : serverExtras));
  }, [serverExtras]);

  const dbQuarters = useMemo(() => (storedFromTags || []).filter(isQuarter), [storedFromTags]);

  const quarterOptions = useMemo(() => {
    const values = new Set(
      [currentQuarter(), selectedQuarter, ...dbQuarters, ...extraQuarters].filter(Boolean)
    );
    return Array.from(values).sort();
  }, [dbQuarters, extraQuarters, selectedQuarter]);

  const saveExtras = (next) => {
    pendingExtrasRef.current = next;
    createOption(VIRTUAL_TAG_QUARTERS_OPTION, { quarters: next }, () => {
      dispatch(getOrganizationOptions(organizationId, true));
    });
  };

  const addQuarter = (raw) => {
    const quarter = String(raw || "").trim().toUpperCase();
    if (!isQuarter(quarter) || quarterOptions.includes(quarter)) {
      return false;
    }
    const next = [...extraQuarters, quarter].sort();
    setExtraQuarters(next);
    saveExtras(next);
    return true;
  };

  const removeQuarter = (quarter) => {
    if (!extraQuarters.includes(quarter)) {
      return;
    }
    const next = extraQuarters.filter((item) => item !== quarter);
    setExtraQuarters(next);
    saveExtras(next);
  };

  return {
    quarterOptions,
    extraQuarters,
    dbQuarters,
    addQuarter,
    removeQuarter,
    isSaving: isCreateOrganizationOptionLoading,
  };
};

export default useVirtualTagQuarterOptions;
