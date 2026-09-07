export const COST_PERIOD_DATE = "date";
export const COST_PERIOD_BILLING = "billing";

export const currentQuarter = (date = new Date()) => {
  const quarter = Math.floor(date.getUTCMonth() / 3) + 1;
  return `${date.getUTCFullYear()}Q${quarter}`;
};

export const isQuarter = (value) => /^\d{4}Q[1-4]$/.test(String(value || ""));

const shiftQuarter = (year: number, quarter: number, delta: number) => {
  let nextYear = year;
  let nextQuarter = quarter + delta;
  while (nextQuarter < 1) {
    nextQuarter += 4;
    nextYear -= 1;
  }
  while (nextQuarter > 4) {
    nextQuarter -= 4;
    nextYear += 1;
  }
  return `${nextYear}Q${nextQuarter}`;
};

export const nearbyQuarters = (date = new Date(), past = 7, future = 1) => {
  const year = date.getUTCFullYear();
  const quarter = Math.floor(date.getUTCMonth() / 3) + 1;
  return Array.from({ length: past + future + 1 }, (_, index) => shiftQuarter(year, quarter, index - past));
};

export const previousQuarter = (quarter: string) => {
  const match = /^(\d{4})Q([1-4])$/.exec(quarter);
  if (!match) {
    return quarter;
  }
  return shiftQuarter(Number(match[1]), Number(match[2]), -1);
};

export const normalizeInvoiceMonths = (raw: unknown): string[] => {
  if (raw == null || raw === "") {
    return [];
  }
  const list = Array.isArray(raw) ? raw : [raw];
  return list.map((value) => String(value)).filter((value) => /^\d{6}$/.test(value));
};

export const toExpensePeriodApiParams = (params: Record<string, unknown> = {}) => {
  const invoiceMonths = normalizeInvoiceMonths(params.invoiceMonths ?? params.invoice_months);
  if (invoiceMonths.length) {
    return { invoice_months: invoiceMonths };
  }
  return {
    start_date: params.startDate ?? params.start_date,
    end_date: params.endDate ?? params.end_date,
  };
};

export const toDateRangeApiParams = (params: Record<string, unknown> = {}) => ({
  start_date: params.startDate ?? params.start_date,
  end_date: params.endDate ?? params.end_date,
});

export const isIncompleteBillingPeriod = (params: Record<string, unknown> = {}) => {
  const invoiceMonths = normalizeInvoiceMonths(params.invoiceMonths ?? params.invoice_months);
  if (params.periodType === COST_PERIOD_BILLING) {
    return invoiceMonths.length === 0;
  }
  return Array.isArray(params.invoiceMonths) && invoiceMonths.length === 0 && params.startDate == null && params.start_date == null;
};

export const formatInvoiceMonthLabel = (yyyymm: string) => {
  const year = Number(yyyymm.slice(0, 4));
  const month = Number(yyyymm.slice(4, 6));
  if (!year || !month) {
    return yyyymm;
  }
  return new Date(Date.UTC(year, month - 1, 1)).toLocaleString("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
};
