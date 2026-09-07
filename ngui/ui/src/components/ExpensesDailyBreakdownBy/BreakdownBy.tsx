import Selector, { Item, ItemContent } from "components/Selector";
import { getVisibleBreakdowns } from "hooks/useBreakdownBy";
import { useHasKubernetesDataSource } from "hooks/useHasKubernetesDataSource";

type BreakdownByProps = {
  value: string;
  onChange: (value: string) => void;
  extraBreakdowns?: { value: string; name: string }[];
};

const BreakdownBy = ({ value, onChange, extraBreakdowns = [] }: BreakdownByProps) => {
  const hasKubernetes = useHasKubernetesDataSource();
  const visibleBreakdowns = getVisibleBreakdowns(hasKubernetes);

  return (
    <Selector id="resource-categorize-by-selector" labelMessageId="categorizeBy" value={value} onChange={onChange}>
      {[...visibleBreakdowns, ...extraBreakdowns].map((breakdown) => (
        <Item key={breakdown.value} value={breakdown.value}>
          <ItemContent>{breakdown.name}</ItemContent>
        </Item>
      ))}
    </Selector>
  );
};

export default BreakdownBy;
