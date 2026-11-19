import type { OperationsActionType } from "../../types";

const reasonLabels: Record<string, string> = {
  ORIGINAL_RUN_UNSUPPORTED: "原运行的版本、参数或可信输入记录不完整，当前无法安全重算。请先核对原记录；系统不会自动替换因子版本。",
  PRICE_RELEASE_UNAVAILABLE: "可信的前复权价格发布缺失或校验不一致。需先核对并恢复数据发布链；普通数据同步不会生成前复权发布。",
  TRUSTED_PRICE_INPUT_MISSING: "原目标窗口缺少可用的可信行情。请先补齐原复权口径和证券范围对应的数据，再预检重算。",
  INPUT_ROW_LIMIT_EXCEEDED: "价格输入超过本次重算的行数上限，需人工核对并安排分批研究运行，不能静默截取输入。",
  SYMBOL_LIMIT_EXCEEDED: "证券范围超过本次重算的数量上限，需人工核对并安排分批研究运行，不能静默漏算证券。",
  PRICE_WARMUP_INSUFFICIENT: "计算窗口之前的历史观测不足。先补齐所需历史输入；反复重算或重建画像不会补出这些数据。",
  UPSTREAM_INPUT_MISSING: "所需上游数据或组成因子缺失。先处理下方缺失依赖，再返回原运行预检。",
  REQUESTED_SYMBOL_PRICE_MISSING: "部分指定证券在原目标窗口没有可信行情。请核对证券及缺失区间，不能当作停牌或零收益处理。",
  TARGET_TRADING_CALENDAR_MISSING: "原目标窗口缺少可核验的开市日历。请先核对日历数据，不能跳过交易日检查。",
  TRUSTED_PRICE_DATES_MISSING: "部分目标交易日没有可信行情。请先核对列出的日期并补齐对应数据。",
  PRICE_RELEASE_DATE_INCOMPLETE: "当前前复权价格发布尚未覆盖原目标窗口。需完成并核验对应的数据发布后再重算。",
};

const dependencyLabels: Record<string, string> = {
  financial_statements: "财报数据",
  generic_valuations: "通用估值数据（当前研究观察不能替代）",
  hot_stock: "热门股票榜",
  skyrocket: "飙升榜",
  limit_up_pool: "涨停池",
  limit_down_pool: "跌停池",
  failed_limit_up: "炸板池",
  limit_up_ladder: "连板梯队",
  dragon_tiger: "龙虎榜",
  eastmoney_main_fund_rank: "东方财富主力资金榜",
};
const hithinkDatasets = new Set([
  "hot_stock", "skyrocket", "limit_up_pool", "limit_down_pool",
  "failed_limit_up", "limit_up_ladder", "dragon_tiger",
]);

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

export interface DiagnosticNextAction {
  actionType: OperationsActionType;
  label: string;
  explanation: string;
  sources?: string[];
}

export function operationDiagnostics(checks: Record<string, unknown>, actionType: OperationsActionType) {
  const codes = strings(checks.reason_codes);
  const dependencies = strings(checks.missing_dependencies);
  const reasons = codes.map((code) => reasonLabels[code] ?? `尚未归类的检查阻断（${code}），请保留原始证据进行人工核对。`);
  const rows: { label: string; value: string }[] = [];
  if (typeof checks.factor_name === "string") rows.push({ label: "目标因子", value: `${checks.factor_name}@${checks.factor_version ?? "未知版本"}` });
  if (typeof checks.target_start_date === "string" && typeof checks.target_end_date === "string") rows.push({ label: "原目标区间", value: `${checks.target_start_date} 至 ${checks.target_end_date}` });
  if (typeof checks.adjust_type === "string") rows.push({ label: "复权口径", value: ({ none: "未复权", qfq: "前复权", hfq: "后复权" } as Record<string, string>)[checks.adjust_type] ?? checks.adjust_type });
  for (const [key, label] of [
    ["input_rows", "可用价格输入行数"], ["target_rows", "目标区间价格行数"],
    ["target_trade_dates", "目标开市日数"], ["available_count", "有可用值的画像候选数"],
    ["missing_count", "缺少可用值的画像候选数"],
  ]) if (typeof checks[key] === "number") rows.push({ label, value: checks[key].toLocaleString("zh-CN") });
  for (const [key, label] of [
    ["missing_price_symbols", "缺少行情的证券示例"],
    ["insufficient_history_symbols", "历史观测不足的证券示例"],
    ["missing_price_dates", "缺少可信行情的日期示例"],
    ["missing_factor_examples", "缺少因子值的候选示例"],
  ]) {
    const items = strings(checks[key]);
    if (items.length) rows.push({ label, value: items.slice(0, 20).join("、") });
  }
  if (dependencies.length) rows.push({ label: "缺失依赖", value: dependencies.slice(0, 20).map((key) => dependencyLabels[key] ?? `${key.includes("@") ? "组成因子" : "数据集"} ${key}`).join("、") });
  const detail = typeof checks.detail === "string" ? checks.detail : "";
  let nextAction: DiagnosticNextAction | null = null;
  let nextStep = codes.length ? "请先处理上述阻断，再返回原因子运行重新预检。重建画像只分析已有因子值，不能补算缺失值。" : "";
  const releaseBlocked = codes.includes("PRICE_RELEASE_UNAVAILABLE") || codes.includes("PRICE_RELEASE_DATE_INCOMPLETE");
  if (releaseBlocked || checks.next_action === "manual_review") {
    nextStep = "需要人工核对原运行或可信数据发布；当前操作面板不能自动重建前复权发布。核对完成后再回到原运行预检。";
  } else if (checks.next_action === "daily_data_sync" && codes.includes("TRUSTED_PRICE_INPUT_MISSING") && checks.adjust_type === "none") {
    nextAction = { actionType: "daily_data_sync", label: "查看数据同步计划", explanation: "仅切换到未复权数据同步表单，仍需重新预检并确认。同步后返回原运行检查缺失窗口是否已补齐。" };
  } else if (checks.next_action === "alternative_data_sync" && codes.includes("UPSTREAM_INPUT_MISSING")) {
    const sources = [...new Set(dependencies.flatMap((key) => hithinkDatasets.has(key) ? ["hithink"] : key === "eastmoney_main_fund_rank" ? ["eastmoney"] : []))].sort();
    if (sources.length) nextAction = { actionType: "alternative_data_sync", label: "查看情绪与资金补采计划", sources, explanation: "仅切换到对应来源的当前补采表单，仍需重新预检并确认。历史缺口不保证能由当前补采补齐，补采后须返回原运行复查。" };
  } else if (checks.next_action === "price_value_input_prepare" && actionType !== "factor_values_rebuild" && actionType !== "factor_profile_rebuild") {
    nextAction = { actionType: "price_value_input_prepare", label: "查看当前研究输入准备计划", explanation: "仅准备当前价格价值研究观察，仍需预检确认；不会补造历史 PIT，也不会替代通用估值因子的输入。" };
  }
  if (actionType === "factor_profile_rebuild" && typeof checks.missing_count === "number" && checks.missing_count > 0) nextStep = "部分候选缺少因子值。请从失败与无观察记录查看原因并先补齐输入、重算因子值；重建画像不会解除这些缺失。";
  return { reasons, rows, detail, nextStep, nextAction };
}
