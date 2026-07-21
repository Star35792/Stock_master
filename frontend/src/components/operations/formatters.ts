export function countEntries(counts: Record<string, number>) {
  return Object.entries(counts).sort(([, left], [, right]) => right - left);
}

export function maxCount(counts: Record<string, number>) {
  return Math.max(1, ...Object.values(counts));
}

export function sumCounts(counts: Record<string, number>) {
  return Object.values(counts).reduce((total, count) => total + count, 0);
}

export function barWidth(count: number, counts: Record<string, number>) {
  return `${Math.max(8, (count / maxCount(counts)) * 100)}%`;
}

export function statusClass(status: string) {
  return `status status-${status.replace(/_/g, "-")}`;
}

export function severityClass(severity: string) {
  return `severity severity-${severity}`;
}

export function formatDate(value: string | null) {
  if (!value) {
    return "-";
  }

  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));
}

export function formatDateTime(value: string | null) {
  if (!value) {
    return "-";
  }

  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function formatNumber(value: number | string | null) {
  if (value === null) {
    return "-";
  }

  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: 2,
  }).format(Number(value));
}

export function formatPercent(value: number | string | null) {
  if (value === null) {
    return "-";
  }

  return new Intl.NumberFormat("zh-CN", {
    style: "percent",
    maximumFractionDigits: 2,
  }).format(Number(value));
}

export function sideLabel(side: string) {
  return side === "buy" ? "买入" : "卖出";
}

export function formatUnknown(value: unknown): string {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "object") {
    return JSON.stringify(value, null, 2);
  }

  return String(value);
}
