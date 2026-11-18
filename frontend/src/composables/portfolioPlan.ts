import { computed, reactive, ref, watch } from "vue";

import {
  createPortfolioPlan,
  createPaperExecutionApproval,
  capturePaperEquitySnapshot,
  createRiskEvaluation,
  fetchPortfolioPlan,
  fetchPortfolioRiskReadiness,
  fetchPaperExecutionApproval,
  fetchPaperExecutionByApproval,
  fetchBacktestFinalHoldings,
  fetchPositionBookHoldings,
  fetchPositionBooks,
  executePaperApproval,
  revokePaperExecutionApproval,
} from "../api";
import { shanghaiDate } from "../components/operations/formatters";
import type {
  PortfolioAssetInput,
  PortfolioPlan,
  PortfolioPlanCreateRequest,
  PortfolioRiskReadiness,
  PaperEquitySnapshot,
  PaperExecutionApproval,
  PaperExecution,
  PositionBook,
  RiskEvaluation,
} from "../types";

/**
 * 组合草案工作流：手工/预填草案表单、风险预算、创建草案、
 * 风险前核对 → 净值快照 → 正式风险评估 → 模拟执行确认 → 执行的完整链路。
 *
 * 逻辑与视图分离，便于独立测试；所有外部依赖（API、日期、研究上下文）
 * 均可注入，视图层只负责渲染与事件接线。
 */
export interface PortfolioPlanWorkflowDeps {
  createPortfolioPlan: typeof createPortfolioPlan;
  createPaperExecutionApproval: typeof createPaperExecutionApproval;
  capturePaperEquitySnapshot: typeof capturePaperEquitySnapshot;
  createRiskEvaluation: typeof createRiskEvaluation;
  fetchPortfolioPlan: typeof fetchPortfolioPlan;
  fetchPortfolioRiskReadiness: typeof fetchPortfolioRiskReadiness;
  fetchPaperExecutionApproval: typeof fetchPaperExecutionApproval;
  fetchPaperExecutionByApproval: typeof fetchPaperExecutionByApproval;
  fetchBacktestFinalHoldings: typeof fetchBacktestFinalHoldings;
  fetchPositionBookHoldings: typeof fetchPositionBookHoldings;
  fetchPositionBooks: typeof fetchPositionBooks;
  executePaperApproval: typeof executePaperApproval;
  revokePaperExecutionApproval: typeof revokePaperExecutionApproval;
  shanghaiDate: () => string;
}

export interface PortfolioPlanWorkflowOptions {
  /** 写入口径：false 时禁用创建/确认/执行等写操作。 */
  writeEnabled?: boolean | (() => boolean);
  /** 外部传入的模拟账户 ID（响应式 getter）。 */
  accountId?: () => number | undefined;
  /** 账户级操作忙碌状态变化回调。 */
  onBusyChange?: (value: boolean) => void;
  /** 研究主线上下文：最近一次回测 run_id，用于预填框自动填入。 */
  getLastRunId?: () => number | null;
}

const defaultDeps: PortfolioPlanWorkflowDeps = {
  createPortfolioPlan,
  createPaperExecutionApproval,
  capturePaperEquitySnapshot,
  createRiskEvaluation,
  fetchPortfolioPlan,
  fetchPortfolioRiskReadiness,
  fetchPaperExecutionApproval,
  fetchPaperExecutionByApproval,
  fetchBacktestFinalHoldings,
  fetchPositionBookHoldings,
  fetchPositionBooks,
  executePaperApproval,
  revokePaperExecutionApproval,
  shanghaiDate,
};

export function createPortfolioPlanWorkflow(
  deps: PortfolioPlanWorkflowDeps = defaultDeps,
  options: PortfolioPlanWorkflowOptions = {},
) {
  const writeEnabled = computed(() => {
    const value = typeof options.writeEnabled === "function" ? options.writeEnabled() : options.writeEnabled;
    return value !== false;
  });

  const newAsset = (): PortfolioAssetInput => ({
    symbol: "",
    security_type: "stock",
    industry: "",
    price: "",
    current_quantity: 0,
    requested_weight: "0",
    lot_size: 100,
  });

  const form = reactive<PortfolioPlanCreateRequest>({
    name: "手工模拟组合草案",
    as_of_date: deps.shanghaiDate(),
    cash: "100000",
    assets: [newAsset(), newAsset()],
    constraints: {
      cash_reserve_rate: "0.05",
      max_symbol_weight: "0.20",
      max_industry_weight: "0.40",
      security_type_budgets: {},
    },
  });
  const riskBudgetEnabled = ref(false);
  const styleBudgetEnabled = ref(false);
  const trackingErrorBudgetEnabled = ref(false);
  const volatilityBudgetEnabled = ref(false);
  const riskBudgetDraft = reactive({
    max_industry_weight: "0.40",
    max_style_exposure: "",
    max_tracking_error: "",
    max_portfolio_volatility: "",
    benchmark: "",
  });

  const isPrefilling = ref(false);
  const prefillBooks = ref<PositionBook[]>([]);
  const prefillBookId = ref<number | null>(null);
  const prefillRunId = ref("");
  const isPrefillingFromRun = ref(false);
  const prefillNote = ref<string | null>(null);

  async function prefillFromBacktestRun() {
    if (isPrefilling.value || isPrefillingFromRun.value) return;
    const runId = Number(prefillRunId.value);
    if (!Number.isFinite(runId) || runId <= 0) {
      errorMessage.value = "请输入有效的回测 run ID。";
      return;
    }
    isPrefillingFromRun.value = true;
    errorMessage.value = null;
    try {
      const report = await deps.fetchBacktestFinalHoldings(runId);
      if (report.holdings.length === 0) {
        errorMessage.value = `回测 #${runId} 最优交易日没有持仓快照，无法预填。`;
        return;
      }
      form.assets = report.holdings.map((row) => ({
        symbol: row.symbol,
        security_type: row.security_type === "etf" ? "etf" : "stock",
        industry: row.industry ?? "",
        price: row.close,
        current_quantity: 0,
        requested_weight: row.weight,
        lot_size: 100,
      }));
      // 复制目标权重且当前持仓为零：全部权益作为草案预算，不能仅使用剩余现金。
      form.cash = report.total_equity;
      form.as_of_date = report.as_of_date;
      prefillNote.value = `已复制回测 #${runId} 截至 ${report.as_of_date} 的目标权重和收盘价，预算 ${report.total_equity} 元。当前持仓设为零；请核对草案日期、预算与现仓。`;
      form.name = `预填自回测 #${runId}（截至 ${report.as_of_date}）`;
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : "回测持仓预填失败";
    } finally {
      isPrefillingFromRun.value = false;
    }
  }

  async function loadPrefillBooks() {
    try {
      prefillBooks.value = await deps.fetchPositionBooks();
      if (prefillBookId.value === null || !prefillBooks.value.some((book) => book.id === prefillBookId.value)) {
        prefillBookId.value = prefillBooks.value[0]?.id ?? null;
      }
    } catch {
      // 账本不可用时保持空列表，预填按钮会给出可读错误。
      prefillBooks.value = [];
      prefillBookId.value = null;
    }
  }

  async function prefillFromPositionBook() {
    if (isPrefilling.value || isPrefillingFromRun.value) return;
    prefillNote.value = null;
    isPrefilling.value = true;
    errorMessage.value = null;
    try {
      await loadPrefillBooks();
      if (prefillBooks.value.length === 0) {
        errorMessage.value = "还没有持仓账本，请先在「持仓分析」页创建账本并记账。";
        return;
      }
      const book =
        prefillBooks.value.find((item) => item.id === prefillBookId.value) ?? prefillBooks.value[0];
      const holdings = await deps.fetchPositionBookHoldings(book.id);
      if (holdings.sequence_anomaly) {
        errorMessage.value = `账本「${book.name}」存在流水顺序异常，请先在持仓页核对。`;
        return;
      }
      const missingQuotes = holdings.holdings.filter((row) => row.quantity > 0 && row.price === null);
      if (missingQuotes.length > 0) {
        errorMessage.value = `账本「${book.name}」有持仓缺可信价：${missingQuotes.map((row) => row.symbol).join("、")}；已停止预填，避免遗漏现仓。`;
        return;
      }
      const priced = holdings.holdings.filter((row) => row.quantity > 0 && row.price !== null);
      if (priced.length === 0) {
        errorMessage.value = `账本「${book.name}」没有可信价持仓，无法预填草案。`;
        return;
      }
      const quoteDates = new Set(priced.map((row) => row.price_as_of));
      if (quoteDates.size !== 1 || quoteDates.has(null)) {
        errorMessage.value = `账本「${book.name}」的持仓报价日期不一致或缺失：${priced.map((row) => `${row.symbol} ${row.price_as_of ?? "无日期"}`).join("、")}；请核对后手工创建草案。`;
        return;
      }
      const quoteDate = priced[0].price_as_of!;
      form.assets = priced.map((row) => ({
        symbol: row.symbol,
        security_type: row.security_type === "etf" ? "etf" : "stock",
        industry: row.industry ?? "",
        price: row.price ?? "0",
        current_quantity: row.quantity,
        requested_weight: row.weight,
        lot_size: 100,
      }));
      // 草案的现金字段必须填账本现金本身：组合总权益由 cash + Σ现价×当前数量
      // 推导（见 builder），当前数量已预填，若填 total_equity 会双计持仓市值。
      form.cash = holdings.cash;
      form.as_of_date = quoteDate;
      form.name = `预填自账本「${book.name}」`;
      prefillNote.value = `已从账本「${book.name}」预填现仓、现金与 ${quoteDate} 的可信报价；草案日期已设为该日。请核对持仓与预算，报价可能不是今日。`;
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : "持仓账本预填失败";
    } finally {
      isPrefilling.value = false;
    }
  }

  const plan = ref<PortfolioPlan | null>(null);
  const lookupId = ref("");
  const approvalLookupId = ref("");
  const isSubmitting = ref(false);
  const isLoading = ref(false);
  const isLoadingApproval = ref(false);
  const isCheckingReadiness = ref(false);
  const errorMessage = ref<string | null>(null);
  const paperAccountId = ref(options.accountId?.() ? String(options.accountId()) : "");
  const readiness = ref<PortfolioRiskReadiness | null>(null);
  const equitySnapshot = ref<PaperEquitySnapshot | null>(null);
  const riskEvaluation = ref<RiskEvaluation | null>(null);
  const approval = ref<PaperExecutionApproval | null>(null);
  const execution = ref<PaperExecution | null>(null);
  const confirmationPhrase = ref("");
  const isCapturingEquity = ref(false);
  const isEvaluatingRisk = ref(false);
  const isApproving = ref(false);
  const isRevokingApproval = ref(false);
  const isExecuting = ref(false);
  const clock = ref(Date.now());
  let equityIdempotencyKey: string | null = null;
  let riskIdempotencyKey: string | null = null;
  let approvalIdempotencyKey: string | null = null;
  let revocationIdempotencyKey: string | null = null;
  let executionIdempotencyKey: string | null = null;
  let pendingIdempotencyKey: string | null = null;
  let pendingPlanFingerprint: string | null = null;
  let clockTimer: number | undefined;
  let accountGeneration = 0;

  const accountBusy = computed(() =>
    isCheckingReadiness.value ||
    isCapturingEquity.value ||
    isEvaluatingRisk.value ||
    isApproving.value ||
    isRevokingApproval.value ||
    isExecuting.value ||
    isLoadingApproval.value,
  );

  const expectedConfirmationPhrase = computed(() =>
    riskEvaluation.value ? `CONFIRM PAPER EXECUTION ${riskEvaluation.value.check_id}` : "",
  );
  const displayedApprovalStatus = computed(() => {
    if (!approval.value) return null;
    if (execution.value) return "consumed";
    if (
      approval.value.effective_status === "pending" &&
      clock.value >= new Date(approval.value.expires_at).getTime()
    ) {
      return "expired";
    }
    return approval.value.effective_status;
  });
  const estimatedCashAfter = computed(() => {
    const value = approval.value?.confirmation_policy.estimated_cash_after;
    return typeof value === "string" ? value : null;
  });

  function start() {
    clockTimer = window.setInterval(() => {
      clock.value = Date.now();
    }, 1000);
    void loadPrefillBooks();
    // 若研究上下文里带过来最近一次回测 run_id，自动填入预填框，串起「因子→回测→组合草案」链路。
    const lastRunId = options.getLastRunId?.() ?? null;
    if (lastRunId !== null) {
      prefillRunId.value = String(lastRunId);
    }
  }

  function stop() {
    window.clearInterval(clockTimer);
  }

  watch(
    form,
    () => {
      pendingIdempotencyKey = null;
    },
    { deep: true },
  );

  if (options.accountId) {
    watch(options.accountId, (value) => {
      accountGeneration += 1;
      paperAccountId.value = value ? String(value) : "";
      readiness.value = null;
      equitySnapshot.value = null;
      riskEvaluation.value = null;
      approval.value = null;
      execution.value = null;
    });
  }

  watch(accountBusy, (value) => options.onBusyChange?.(value), { immediate: true });

  function makeIdempotencyKey() {
    return crypto.randomUUID();
  }

  function requiredBudgetValue(value: string, label: string): string {
    const trimmed = value.trim();
    if (!trimmed) throw new Error(`请填写${label}。`);
    return trimmed;
  }

  function buildPlanRequest(): PortfolioPlanCreateRequest {
    const constraints = { ...form.constraints };
    if (riskBudgetEnabled.value) {
      const benchmark = riskBudgetDraft.benchmark.trim().toUpperCase();
      if (trackingErrorBudgetEnabled.value && !benchmark) {
        throw new Error("启用跟踪误差预算时须填写基准代码。");
      }
      constraints.risk_budget = {
        max_industry_weight: requiredBudgetValue(riskBudgetDraft.max_industry_weight, "行业风险上限"),
        max_style_exposure: styleBudgetEnabled.value
          ? requiredBudgetValue(riskBudgetDraft.max_style_exposure, "三因子代理暴露上限") : null,
        max_tracking_error: trackingErrorBudgetEnabled.value
          ? requiredBudgetValue(riskBudgetDraft.max_tracking_error, "年化跟踪误差上限") : null,
        max_portfolio_volatility: volatilityBudgetEnabled.value
          ? requiredBudgetValue(riskBudgetDraft.max_portfolio_volatility, "年化波动率上限") : null,
        benchmark: trackingErrorBudgetEnabled.value ? benchmark : null,
      };
    } else {
      delete constraints.risk_budget;
    }
    return {
      ...form,
      assets: form.assets.map((asset) => ({ ...asset })),
      constraints,
    };
  }

  async function submitPlan() {
    if (!writeEnabled.value || isSubmitting.value) return;
    isSubmitting.value = true;
    errorMessage.value = null;
    try {
      const request = buildPlanRequest();
      const fingerprint = JSON.stringify(request);
      if (pendingPlanFingerprint !== fingerprint) {
        pendingPlanFingerprint = fingerprint;
        pendingIdempotencyKey = makeIdempotencyKey();
      }
      plan.value = await deps.createPortfolioPlan(request, pendingIdempotencyKey!);
      readiness.value = null;
      equitySnapshot.value = null;
      riskEvaluation.value = null;
      approval.value = null;
      execution.value = null;
      confirmationPhrase.value = "";
      lookupId.value = String(plan.value.plan_id);
      pendingIdempotencyKey = null;
      pendingPlanFingerprint = null;
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : "组合草案创建失败";
    } finally {
      isSubmitting.value = false;
    }
  }

  async function loadPlan() {
    const planId = Number(lookupId.value);
    if (!Number.isInteger(planId) || planId <= 0 || isLoading.value) {
      errorMessage.value = "请输入有效的草案 ID";
      return;
    }
    isLoading.value = true;
    errorMessage.value = null;
    try {
      plan.value = await deps.fetchPortfolioPlan(planId);
      readiness.value = null;
      equitySnapshot.value = null;
      riskEvaluation.value = null;
      approval.value = null;
      execution.value = null;
      confirmationPhrase.value = "";
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : "组合草案读取失败";
    } finally {
      isLoading.value = false;
    }
  }

  async function captureEquity() {
    if (!plan.value || !readiness.value?.ready_for_risk_check || isCapturingEquity.value) return;
    const accountId = Number(paperAccountId.value);
    const generation = accountGeneration;
    isCapturingEquity.value = true;
    errorMessage.value = null;
    equityIdempotencyKey ??= makeIdempotencyKey();
    try {
      const result = await deps.capturePaperEquitySnapshot(
        accountId,
        plan.value.as_of_date,
        equityIdempotencyKey,
      );
      if (generation !== accountGeneration) return;
      equitySnapshot.value = result;
      riskEvaluation.value = null;
      approval.value = null;
      execution.value = null;
      confirmationPhrase.value = "";
      equityIdempotencyKey = null;
    } catch (error) {
      if (generation !== accountGeneration) return;
      errorMessage.value = error instanceof Error ? error.message : "账户净值快照创建失败";
    } finally {
      isCapturingEquity.value = false;
    }
  }

  async function evaluateRisk() {
    if (!plan.value || !equitySnapshot.value || isEvaluatingRisk.value) return;
    const accountId = Number(paperAccountId.value);
    const generation = accountGeneration;
    isEvaluatingRisk.value = true;
    errorMessage.value = null;
    riskIdempotencyKey ??= makeIdempotencyKey();
    try {
      const result = await deps.createRiskEvaluation(
        plan.value.plan_id,
        accountId,
        equitySnapshot.value.snapshot_id,
        riskIdempotencyKey,
      );
      if (generation !== accountGeneration) return;
      riskEvaluation.value = result;
      approval.value = null;
      execution.value = null;
      confirmationPhrase.value = "";
      riskIdempotencyKey = null;
    } catch (error) {
      if (generation !== accountGeneration) return;
      errorMessage.value = error instanceof Error ? error.message : "正式风险评估失败";
    } finally {
      isEvaluatingRisk.value = false;
    }
  }

  async function confirmPaperExecution() {
    if (
      !writeEnabled.value ||
      !riskEvaluation.value?.risk_passed ||
      confirmationPhrase.value !== expectedConfirmationPhrase.value ||
      isApproving.value
    ) return;
    isApproving.value = true;
    const generation = accountGeneration;
    errorMessage.value = null;
    approvalIdempotencyKey ??= makeIdempotencyKey();
    try {
      const result = await deps.createPaperExecutionApproval(
        riskEvaluation.value.check_id,
        riskEvaluation.value.evaluation_hash,
        confirmationPhrase.value,
        approvalIdempotencyKey,
      );
      if (generation !== accountGeneration) return;
      approval.value = result;
      execution.value = null;
      approvalIdempotencyKey = null;
    } catch (error) {
      if (generation !== accountGeneration) return;
      errorMessage.value = error instanceof Error ? error.message : "模拟执行确认失败";
    } finally {
      isApproving.value = false;
    }
  }

  async function executeApprovedPaperBatch() {
    if (
      !writeEnabled.value ||
      !approval.value ||
      displayedApprovalStatus.value !== "pending" ||
      isExecuting.value
    ) return;
    const confirmed = window.confirm(
      `执行 ${approval.value.order_summary.length} 笔本地模拟交易？卖单先于买单，且不会向券商、交易所或真实账户发送订单。`,
    );
    if (!confirmed) return;
    const activeApproval = approval.value;
    isExecuting.value = true;
    const generation = accountGeneration;
    errorMessage.value = null;
    executionIdempotencyKey ??= makeIdempotencyKey();
    try {
      const result = await deps.executePaperApproval(
        activeApproval.approval_id,
        activeApproval.approval_hash,
        executionIdempotencyKey,
      );
      if (generation !== accountGeneration) return;
      execution.value = result;
      executionIdempotencyKey = null;
    } catch (error) {
      if (generation !== accountGeneration) return;
      try {
        const result = await deps.fetchPaperExecutionByApproval(activeApproval.approval_id);
        if (generation !== accountGeneration) return;
        execution.value = result;
        executionIdempotencyKey = null;
      } catch {
        errorMessage.value = error instanceof Error ? error.message : "模拟交易执行失败";
      }
    } finally {
      isExecuting.value = false;
    }
  }

  async function loadApproval() {
    const approvalId = Number(approvalLookupId.value);
    if (!Number.isInteger(approvalId) || approvalId <= 0 || isLoadingApproval.value) {
      errorMessage.value = "请输入有效的确认 ID";
      return;
    }
    isLoadingApproval.value = true;
    const generation = accountGeneration;
    errorMessage.value = null;
    try {
      const loaded = await deps.fetchPaperExecutionApproval(approvalId);
      if (generation !== accountGeneration) return;
      if (plan.value && loaded.plan_id !== plan.value.plan_id) {
        throw new Error("确认记录不属于当前组合草案");
      }
      const selectedAccountId = Number(paperAccountId.value);
      if (!Number.isInteger(selectedAccountId) || loaded.account_id !== selectedAccountId) {
        throw new Error("确认记录不属于当前选择的模拟账户");
      }
      approval.value = loaded;
      execution.value = null;
      if (loaded.effective_status === "consumed") {
        execution.value = await deps.fetchPaperExecutionByApproval(loaded.approval_id);
      }
    } catch (error) {
      if (generation !== accountGeneration) return;
      errorMessage.value = error instanceof Error ? error.message : "确认记录读取失败";
    } finally {
      isLoadingApproval.value = false;
    }
  }

  async function revokeApproval() {
    if (!approval.value || displayedApprovalStatus.value !== "pending" || isRevokingApproval.value) return;
    isRevokingApproval.value = true;
    const generation = accountGeneration;
    errorMessage.value = null;
    revocationIdempotencyKey ??= makeIdempotencyKey();
    try {
      const result = await deps.revokePaperExecutionApproval(
        approval.value.approval_id,
        "operator cancelled",
        revocationIdempotencyKey,
      );
      if (generation !== accountGeneration) return;
      approval.value = result;
      revocationIdempotencyKey = null;
    } catch (error) {
      if (generation !== accountGeneration) return;
      errorMessage.value = error instanceof Error ? error.message : "模拟执行确认撤销失败";
    } finally {
      isRevokingApproval.value = false;
    }
  }

  async function checkReadiness() {
    if (!plan.value || !writeEnabled.value || isCheckingReadiness.value) return;
    const accountId = Number(paperAccountId.value);
    if (!Number.isInteger(accountId) || accountId <= 0) {
      errorMessage.value = "请输入有效的模拟账户 ID";
      return;
    }
    isCheckingReadiness.value = true;
    const generation = accountGeneration;
    errorMessage.value = null;
    try {
      const result = await deps.fetchPortfolioRiskReadiness(plan.value.plan_id, accountId);
      if (generation !== accountGeneration) return;
      readiness.value = result;
    } catch (error) {
      if (generation !== accountGeneration) return;
      errorMessage.value = error instanceof Error ? error.message : "风险前数据核对失败";
    } finally {
      isCheckingReadiness.value = false;
    }
  }

  function hashFingerprint(value: string) {
    return `${value.slice(0, 10)}…${value.slice(-8)}`;
  }

  function formatDateTime(value: string) {
    return new Intl.DateTimeFormat("zh-CN", {
      dateStyle: "short",
      timeStyle: "medium",
    }).format(new Date(value));
  }

  function formatDecimal(value: string) {
    return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 6 }).format(Number(value));
  }

  function formatPercent(value: string) {
    return `${(Number(value) * 100).toFixed(2)}%`;
  }

  const reasonLabels: Record<string, string> = {
    max_symbol_weight: "单标的上限",
    security_type_budget: "证券类型预算",
    industry_budget: "行业预算",
    cash_reserve: "现金留存",
    lot_rounding: "整手取整",
    style_budget: "三因子代理预算",
    tracking_error_budget: "跟踪误差预算",
    volatility_budget: "波动率预算",
  };
  const styleLabels: Record<string, string> = {
    momentum: "动量",
    volatility: "波动率",
    liquidity: "流动性",
    size: "规模",
    value: "估值",
  };

  const industrySlices = computed(() => {
    const map = new Map<string, { weight: number; items: string[] }>();
    for (const target of plan.value?.targets ?? []) {
      const industry = target.industry?.trim() || "未分类";
      const weight = Number(target.final_weight);
      if (!Number.isFinite(weight)) continue;
      const entry = map.get(industry) ?? { weight: 0, items: [] };
      entry.weight += weight;
      entry.items.push(target.symbol);
      map.set(industry, entry);
    }
    return [...map.entries()].map(([key, value]) => ({ key, weight: value.weight, items: value.items }));
  });

  const symbolSlices = computed(() =>
    (plan.value?.targets ?? [])
      .map((target) => ({ key: target.symbol, weight: Number(target.final_weight), items: [target.symbol] }))
      .filter((slice) => Number.isFinite(slice.weight)),
  );

  return {
    form,
    newAsset,
    riskBudgetEnabled,
    styleBudgetEnabled,
    trackingErrorBudgetEnabled,
    volatilityBudgetEnabled,
    riskBudgetDraft,
    isPrefilling,
    prefillBooks,
    prefillBookId,
    prefillRunId,
    isPrefillingFromRun,
    prefillNote,
    prefillFromBacktestRun,
    prefillFromPositionBook,
    plan,
    lookupId,
    approvalLookupId,
    isSubmitting,
    isLoading,
    isLoadingApproval,
    isCheckingReadiness,
    errorMessage,
    paperAccountId,
    readiness,
    equitySnapshot,
    riskEvaluation,
    approval,
    execution,
    confirmationPhrase,
    isCapturingEquity,
    isEvaluatingRisk,
    isApproving,
    isRevokingApproval,
    isExecuting,
    writeEnabled,
    expectedConfirmationPhrase,
    displayedApprovalStatus,
    estimatedCashAfter,
    buildPlanRequest,
    submitPlan,
    loadPlan,
    captureEquity,
    evaluateRisk,
    confirmPaperExecution,
    executeApprovedPaperBatch,
    loadApproval,
    revokeApproval,
    checkReadiness,
    hashFingerprint,
    formatDateTime,
    formatDecimal,
    formatPercent,
    reasonLabels,
    styleLabels,
    industrySlices,
    symbolSlices,
    start,
    stop,
  };
}

export type PortfolioPlanWorkflow = ReturnType<typeof createPortfolioPlanWorkflow>;
