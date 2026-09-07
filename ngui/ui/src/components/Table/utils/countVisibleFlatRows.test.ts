import { countVisibleFlatRows } from "./countVisibleFlatRows";

describe("countVisibleFlatRows", () => {
  const getSubRows = (row) => row.children;

  const data = [
    {
      id: "tenant",
      children: Array.from({ length: 92 }, (_, i) => ({ id: `c${i}` })),
    },
    { id: "other" },
  ];

  it("counts only roots when nothing is expanded", () => {
    expect(countVisibleFlatRows(data, {}, getSubRows, (row) => row.id)).toBe(2);
  });

  it("includes children of expanded parents only", () => {
    expect(countVisibleFlatRows(data, { tenant: true }, getSubRows, (row) => row.id)).toBe(94);
  });

  it("uses default index ids when getRowId is omitted", () => {
    expect(countVisibleFlatRows(data, {}, getSubRows)).toBe(2);
    expect(countVisibleFlatRows(data, { "0": true }, getSubRows)).toBe(94);
  });

  it("counts full tree when expanded is true", () => {
    expect(countVisibleFlatRows(data, true, getSubRows, (row) => row.id)).toBe(94);
  });
});
