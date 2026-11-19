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

const statusLabels: Record<string, string> = {
  pending: "待处理",
  queued: "等待执行",
  running: "运行中",
  success: "成功",
  partial: "部分完成",
  fixed: "已修复",
  reopened: "已重新打开",
  false_positive: "误报",
  completed: "已完成",
  failed: "失败",
  rejected: "已拒绝",
  cancelled: "已取消",
  revoked: "已撤销",
  expired: "已过期",
  consumed: "已执行",
  open: "待处理",
  resolved: "已解决",
  ignored: "已忽略",
};

export function statusLabel(status: string) {
  return statusLabels[status] ?? status;
}

export function qualityStatusLabel(status: string) {
  return ({ trusted: "可信", manual_fixed: "人工修复", suspect: "可疑", rejected: "拒绝" } as Record<string, string>)[status] ?? status;
}

export function securityTypeLabel(type: string) {
  return ({ stock: "股票", etf: "ETF", index: "指数" } as Record<string, string>)[type.toLowerCase()] ?? type;
}

export function historyStatusLabel(status: string) {
  return ({ bootstrap: "监控起点", continuous: "连续", gap: "存在缺口" } as Record<string, string>)[status] ?? status;
}

export function strategyLabel(strategy: string) {
  return ({ factor_selection: "因子选股", buy_and_hold_smoke: "买入持有验证" } as Record<string, string>)[strategy] ?? strategy;
}

export function severityClass(severity: string) {
  return `severity severity-${severity}`;
}

export function severityLabel(severity: string) {
  return ({ info: "提示", warning: "警告", critical: "严重", low: "低", medium: "中", high: "高" } as Record<string, string>)[severity] ?? severity;
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

export function shanghaiDate(value: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(value);
  const part = (type: string) => parts.find((item) => item.type === type)?.value;
  return `${part("year")}-${part("month")}-${part("day")}`;
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

export function formatPctChange(value: number | string | null) {
  if (value === null) return "-";
  return `${new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value))}%`;
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
