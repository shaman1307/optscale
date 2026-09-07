import { POOL_TYPE_BUDGET } from "utils/constants";
import { buildPoolTypeGroupRows, isPoolTypeGroup } from "utils/pools";

type ItemWithValue = {
  value: string;
  name?: unknown;
  parent_id?: string | null;
  purpose?: string;
  isPoolTypeGroup?: boolean;
  selectable?: boolean;
};

export type SelectionTreeNode<T extends ItemWithValue> = {
  item: T;
  children: SelectionTreeNode<T>[];
};

/** Group items by key, sort groups, and sort items within each group by name. */
export const groupItemsByKey = <T extends ItemWithValue>(
  items: T[],
  groupBy: (item: T) => string,
  sortGroups?: (a: string, b: string) => number
): { key: string; items: T[] }[] => {
  const groups = new Map<string, T[]>();
  items.forEach((item) => {
    const key = groupBy(item);
    const list = groups.get(key) || [];
    list.push(item);
    groups.set(key, list);
  });
  const keys = Array.from(groups.keys()).sort(sortGroups || ((a, b) => a.localeCompare(b)));
  return keys.map((key) => {
    const groupItems = [...(groups.get(key) || [])].sort((a, b) => {
      const nameA = String(a.name ?? a.value);
      const nameB = String(b.name ?? b.value);
      return nameA.localeCompare(nameB);
    });
    return { key, items: groupItems };
  });
};

/** Select or deselect all values of a group relative to the current selection. */
export const toggleGroupSelection = (selectedValues: string[], groupValues: string[], checked: boolean): string[] => {
  if (checked) {
    return Array.from(new Set([...selectedValues, ...groupValues]));
  }
  const remove = new Set(groupValues);
  return selectedValues.filter((value) => !remove.has(value));
};

const isSelectablePoolItem = <T extends ItemWithValue>(item: T) => item.selectable !== false && !isPoolTypeGroup(item);

/**
 * Nested sub-pools only (depth >= 2 among real pools).
 * Skips organization root and its direct children (e.g. project pools),
 * keeps service asset pools nested under those projects.
 */
export const getOnlySubpoolValues = <T extends ItemWithValue>(items: T[]): string[] => {
  const pools = items.filter(isSelectablePoolItem);
  const byId = new Map(pools.map((item) => [item.value, item]));

  const depthOf = (item: T): number => {
    let depth = 0;
    let current: T | undefined = item;
    const seen = new Set<string>();
    while (current?.parent_id && byId.has(current.parent_id) && !seen.has(current.value)) {
      seen.add(current.value);
      depth += 1;
      current = byId.get(current.parent_id);
      if (depth > 50) {
        break;
      }
    }
    return depth;
  };

  return pools.filter((item) => depthOf(item) >= 2).map((item) => item.value);
};

/** Collect selectable values under a tree node (node itself + descendants). */
export const collectSelectableTreeValues = <T extends ItemWithValue>(node: SelectionTreeNode<T>): string[] => {
  const values: string[] = [];
  const walk = (current: SelectionTreeNode<T>) => {
    if (isSelectablePoolItem(current.item)) {
      values.push(current.item.value);
    }
    current.children.forEach(walk);
  };
  walk(node);
  return values;
};

type PoolLike = {
  id: string;
  name: string;
  purpose?: string;
  parent_id?: string | null;
  [key: string]: unknown;
};

/**
 * Build the same hierarchy as Pools tab: pool → type groups → child pools → …
 */
export const buildPoolFilterTree = <T extends PoolLike>(
  pools: T[]
): SelectionTreeNode<
  T & {
    value: string;
    selectable: boolean;
    isPoolTypeGroup?: boolean;
    childrenCount?: number;
  }
>[] => {
  type NodeItem = T & {
    value: string;
    selectable: boolean;
    isPoolTypeGroup?: boolean;
    childrenCount?: number;
  };

  const byId = new Map(pools.map((pool) => [pool.id, pool]));
  const byParent = new Map<string | null, T[]>();

  pools.forEach((pool) => {
    const parentId = pool.parent_id && byId.has(pool.parent_id) ? pool.parent_id : null;
    const list = byParent.get(parentId) || [];
    list.push(pool);
    byParent.set(parentId, list);
  });

  const sortByName = (left: T, right: T) => String(left.name || "").localeCompare(String(right.name || ""));

  const buildTypeGroupNodes = (parentId: string): SelectionTreeNode<NodeItem>[] => {
    const children = [...(byParent.get(parentId) || [])].sort(sortByName);
    if (!children.length) {
      return [];
    }

    return buildPoolTypeGroupRows(parentId, children).map((group) => {
      const groupPools = children.filter((child) => (child.purpose || POOL_TYPE_BUDGET) === group.purpose);
      return {
        item: {
          ...(group as unknown as T),
          value: group.id,
          selectable: false,
          isPoolTypeGroup: true,
          childrenCount: group.childrenCount,
        } as NodeItem,
        children: groupPools.map((pool) => ({
          item: {
            ...pool,
            value: pool.id,
            selectable: true,
          },
          children: buildTypeGroupNodes(pool.id),
        })),
      };
    });
  };

  const roots = [...(byParent.get(null) || [])].sort(sortByName);

  return roots.map((root) => ({
    item: {
      ...root,
      value: root.id,
      selectable: true,
    },
    children: buildTypeGroupNodes(root.id),
  }));
};

/** Flatten tree nodes to a list (pools + type-group rows) for search/select-all helpers. */
export const flattenSelectionTree = <T extends ItemWithValue>(nodes: SelectionTreeNode<T>[]): T[] => {
  const items: T[] = [];
  const walk = (node: SelectionTreeNode<T>) => {
    items.push(node.item);
    node.children.forEach(walk);
  };
  nodes.forEach(walk);
  return items;
};
