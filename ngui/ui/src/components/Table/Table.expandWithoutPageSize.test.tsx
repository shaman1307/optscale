import {
  createColumnHelper,
  createTable,
  getCoreRowModel,
  getExpandedRowModel,
  getPaginationRowModel,
} from "@tanstack/react-table";

const columnHelper = createColumnHelper<{ id: string; name: string; children?: { id: string; name: string }[] }>();

const columns = [columnHelper.accessor("name", { header: "Name" })];

const data = [
  {
    id: "root",
    name: "ParentPool",
    children: [
      { id: "child-a", name: "TypeGroupA" },
      { id: "child-b", name: "TypeGroupB" },
    ],
  },
];

const buildTable = (options: { withPaginationRowModel: boolean }) => {
  const table = createTable({
    data,
    columns,
    state: {
      expanded: { root: true },
      pagination: { pageIndex: 0, pageSize: data.length },
    },
    getRowId: (row) => row.id,
    getSubRows: (row) => row.children,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    // Same contract as useExpandedTableSettings: keep children on the parent page.
    paginateExpandedRows: false,
    ...(options.withPaginationRowModel ? { getPaginationRowModel: getPaginationRowModel() } : {}),
    onStateChange: () => {},
    renderFallbackValue: null,
  });

  return table.getRowModel().rows.map((row) => row.original.name);
};

describe("expanded rows without pageSize", () => {
  it("does not flatten children when getPaginationRowModel is missing", () => {
    // Documents TanStack behavior that broke Pools (no pageSize → no pagination model).
    expect(buildTable({ withPaginationRowModel: false })).toEqual(["ParentPool"]);
  });

  it("flattens expanded children when getPaginationRowModel is registered", () => {
    // Regression for usePaginationTableSettings always attaching getPaginationRowModel.
    expect(buildTable({ withPaginationRowModel: true })).toEqual(["ParentPool", "TypeGroupA", "TypeGroupB"]);
  });
});
