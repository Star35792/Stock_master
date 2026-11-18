export type EquityObservation = { trade_date: string; total_equity: string };

export type NormalizedEquity = {
  dates: string[];
  returns: number[];
  drawdowns: number[];
};

// These numbers position a read-only chart. Persisted financial values stay in the backend's DECIMAL columns.
export function normalizeEquity(initialCash: string, rows: EquityObservation[]): NormalizedEquity {
  const initial = Number(initialCash);
  if (!Number.isFinite(initial) || initial <= 0) throw new Error("初始资金无效");
  if (rows.length < 2) throw new Error("净值记录不足两日");
  const dates: string[] = [];
  const returns: number[] = [];
  const drawdowns: number[] = [];
  let peak = initial;
  for (const row of rows) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(row.trade_date) || (dates.length && row.trade_date <= dates[dates.length - 1])) {
      throw new Error("净值日期重复或无序");
    }
    const equity = Number(row.total_equity);
    if (!Number.isFinite(equity) || equity <= 0) throw new Error("净值含非正数或无效值");
    peak = Math.max(peak, equity);
    const totalReturn = equity / initial - 1;
    const drawdown = equity / peak - 1;
    if (!Number.isFinite(totalReturn) || !Number.isFinite(drawdown)) throw new Error("绘图比例无效");
    dates.push(row.trade_date);
    returns.push(totalReturn);
    drawdowns.push(drawdown);
  }
  return { dates, returns, drawdowns };
}

export function sameEquityDates(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((date, index) => date === right[index]);
}

function sampleIndices(values: number[]): number[] {
  if (values.length <= 500) return values.map((_, index) => index);
  const indices = [0, 1];
  const bucketSize = Math.ceil((values.length - 2) / 240);
  for (let start = 2; start < values.length - 1; start += bucketSize) {
    const end = Math.min(start + bucketSize, values.length - 1);
    let low = start;
    let high = start;
    for (let index = start + 1; index < end; index++) {
      if (values[index] < values[low]) low = index;
      if (values[index] > values[high]) high = index;
    }
    indices.push(...(low === high ? [low] : [low, high].sort((a, b) => a - b)));
  }
  indices.push(values.length - 1);
  return indices;
}

export function curvePolyline(values: number[], minimum: number, maximum: number): string {
  if (values.length < 2 || maximum <= minimum) return "";
  return sampleIndices(values).map(index => {
    const x = 45 + (index / (values.length - 1)) * 745;
    const y = 208 - ((values[index] - minimum) / (maximum - minimum)) * 196;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
}
