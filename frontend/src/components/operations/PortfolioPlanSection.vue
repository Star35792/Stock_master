<script setup lang="ts">
import { ClipboardList, Play, Plus, Search, ShieldCheck, Trash2, XCircle } from "@lucide/vue";
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";

import {
  createPortfolioPlan,
  createPaperExecutionApproval,
  capturePaperEquitySnapshot,
  createRiskEvaluation,
  fetchPortfolioPlan,
  fetchPortfolioRiskReadiness,
  fetchPaperExecutionApproval,
  fetchPaperExecutionByApproval,
  fetchPositionBookHoldings,
  fetchPositionBooks,
  executePaperApproval,
  revokePaperExecutionApproval,
} from "../../api";
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
} from "../../types";
import { historyStatusLabel, statusLabel } from "./formatters";

const props = defineProps<{ writeEnabled: boolean; accountId?: number }>();
const emit = defineEmits<{ busyChange: [value: boolean] }>();

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
  as_of_date: new Date().toISOString().slice(0, 10),
  cash: "100000",
  assets: [newAsset(), newAsset()],
  constraints: {
    cash_reserve_rate: "0.05",
    max_symbol_weight: "0.20",
    max_industry_weight: "0.40",
    security_type_budgets: {},
  },
});

const isPrefilling = ref(false);
const prefillBooks = ref<PositionBook[]>([]);
const prefillBookId = ref<number | null>(null);

async function loadPrefillBooks() {
  try {
    prefillBooks.value = await fetchPositionBooks();
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
    const holdings = await fetchPositionBookHoldings(book.id);
    const priced = holdings.holdings.filter((row) => row.price !== null);
    if (priced.length === 0) {
      errorMessage.value = `账本「${book.name}」没有可信价持仓，无法预填草案。`;
      return;
    }
    form.assets = priced.map((row) => ({
      symbol: row.symbol,
      security_type: row.security_type === "etf" ? "etf" : "stock",
      industry: row.industry ?? "",
      price: row.price ?? "0",
      current_quantity: row.quantity,
      requested_weight: String(Number(row.weight)),
      lot_size: 100,
    }));
    // 草案的现金字段必须填账本现金本身：组合总权益由 cash + Σ现价×当前数量
    // 推导（见 builder），当前数量已预填，若填 total_equity 会双计持仓市值。
    form.cash = String(Number(holdings.cash));
    form.name = `预填自账本「${book.name}」`;
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
const paperAccountId = ref(props.accountId ? String(props.accountId) : "");
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

onMounted(() => {
  clockTimer = window.setInterval(() => {
    clock.value = Date.now();
  }, 1000);
  void loadPrefillBooks();
});

onUnmounted(() => window.clearInterval(clockTimer));

watch(
  form,
  () => {
    pendingIdempotencyKey = null;
  },
  { deep: true },
);

watch(
  () => props.accountId,
  (value) => {
    accountGeneration += 1;
    paperAccountId.value = value ? String(value) : "";
    readiness.value = null;
    equitySnapshot.value = null;
    riskEvaluation.value = null;
    approval.value = null;
    execution.value = null;
  },
);

watch(accountBusy, (value) => emit("busyChange", value), { immediate: true });

function makeIdempotencyKey() {
  return crypto.randomUUID();
}

async function submitPlan() {
  if (!props.writeEnabled || isSubmitting.value) return;
  isSubmitting.value = true;
  errorMessage.value = null;
  pendingIdempotencyKey ??= makeIdempotencyKey();
  try {
    plan.value = await createPortfolioPlan(form, pendingIdempotencyKey);
    readiness.value = null;
    equitySnapshot.value = null;
    riskEvaluation.value = null;
    approval.value = null;
    execution.value = null;
    confirmationPhrase.value = "";
    lookupId.value = String(plan.value.plan_id);
    pendingIdempotencyKey = null;
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
    plan.value = await fetchPortfolioPlan(planId);
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
    const result = await capturePaperEquitySnapshot(
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
    const result = await createRiskEvaluation(
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
    !props.writeEnabled ||
    !riskEvaluation.value?.risk_passed ||
    confirmationPhrase.value !== expectedConfirmationPhrase.value ||
    isApproving.value
  ) return;
  isApproving.value = true;
  const generation = accountGeneration;
  errorMessage.value = null;
  approvalIdempotencyKey ??= makeIdempotencyKey();
  try {
    const result = await createPaperExecutionApproval(
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
    !props.writeEnabled ||
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
    const result = await executePaperApproval(
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
      const result = await fetchPaperExecutionByApproval(activeApproval.approval_id);
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
    const loaded = await fetchPaperExecutionApproval(approvalId);
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
      execution.value = await fetchPaperExecutionByApproval(loaded.approval_id);
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
    const result = await revokePaperExecutionApproval(
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

function hashFingerprint(value: string) {
  return `${value.slice(0, 10)}…${value.slice(-8)}`;
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(new Date(value));
}

async function checkReadiness() {
  if (!plan.value || !props.writeEnabled || isCheckingReadiness.value) return;
  const accountId = Number(paperAccountId.value);
  if (!Number.isInteger(accountId) || accountId <= 0) {
    errorMessage.value = "请输入有效的模拟账户 ID";
    return;
  }
  isCheckingReadiness.value = true;
  const generation = accountGeneration;
  errorMessage.value = null;
  try {
    const result = await fetchPortfolioRiskReadiness(plan.value.plan_id, accountId);
    if (generation !== accountGeneration) return;
    readiness.value = result;
  } catch (error) {
    if (generation !== accountGeneration) return;
    errorMessage.value = error instanceof Error ? error.message : "风险前数据核对失败";
  } finally {
    isCheckingReadiness.value = false;
  }
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
};
</script>

<template>
  <section id="portfolio" class="dashboard-section">
    <div class="section-title">
      <ClipboardList :size="20" aria-hidden="true" />
      <h2>组合草案</h2>
      <span class="readonly-pill">手工模拟快照 · 未风控 · 不可执行</span>
    </div>

    <div class="portfolio-layout">
      <form class="portfolio-form" @submit.prevent="submitPlan">
        <div class="form-grid">
          <label>草案名称<input v-model.trim="form.name" required maxlength="100" /></label>
          <label>快照日期<input v-model="form.as_of_date" required type="date" /></label>
          <label>现金<input v-model="form.cash" required inputmode="decimal" /></label>
          <label>现金留存率<input v-model="form.constraints.cash_reserve_rate" required inputmode="decimal" /></label>
          <label>单标的上限<input v-model="form.constraints.max_symbol_weight" required inputmode="decimal" /></label>
          <label>行业上限<input v-model="form.constraints.max_industry_weight" required inputmode="decimal" /></label>
        </div>

        <div class="asset-editor">
          <div class="asset-editor-header">
            <strong>手工标的快照</strong>
            <div class="asset-editor-actions">
              <select
                v-if="prefillBooks.length > 1"
                v-model.number="prefillBookId"
                class="prefill-book-select"
                aria-label="预填来源账本"
              >
                <option v-for="book in prefillBooks" :key="book.id" :value="book.id">
                  {{ book.name }}
                </option>
              </select>
              <button type="button" class="secondary-action" :disabled="isPrefilling" @click="prefillFromPositionBook">
                {{ isPrefilling ? "预填中…" : "从持仓账本预填" }}
              </button>
              <button type="button" class="secondary-action" @click="form.assets.push(newAsset())">
                <Plus :size="16" />增加标的
              </button>
            </div>
          </div>
          <div v-for="(asset, index) in form.assets" :key="index" class="asset-row">
            <label>代码<input v-model.trim="asset.symbol" required maxlength="20" /></label>
            <label>类型
              <select v-model="asset.security_type">
                <option value="stock">股票</option><option value="etf">ETF</option><option value="index">指数</option>
              </select>
            </label>
            <label>行业<input v-model.trim="asset.industry" required maxlength="100" /></label>
            <label>价格<input v-model="asset.price" required inputmode="decimal" /></label>
            <label>当前数量<input v-model.number="asset.current_quantity" required min="0" type="number" /></label>
            <label>目标权重<input v-model="asset.requested_weight" required inputmode="decimal" /></label>
            <label>交易单位<input v-model.number="asset.lot_size" required min="1" type="number" /></label>
            <button
              type="button"
              class="remove-action"
              :disabled="form.assets.length === 1"
              aria-label="删除标的"
              @click="form.assets.splice(index, 1)"
            ><Trash2 :size="16" /></button>
          </div>
        </div>

        <p class="form-warning">价格、持仓、行业和现金均为操作者手工输入，仅用于草案计算，不代表可信账户或实时行情。</p>
        <p v-if="!writeEnabled" class="inline-error">当前为演示数据模式，禁止创建草案。连接后端后才会启用写入。</p>
        <p v-if="errorMessage" class="inline-error">{{ errorMessage }}</p>
        <button class="primary-action" type="submit" :disabled="!writeEnabled || isSubmitting">
          {{ isSubmitting ? "保存中…" : "保存不可执行草案" }}
        </button>
      </form>

      <div class="portfolio-result">
        <div class="lookup-row">
          <input v-model="lookupId" inputmode="numeric" placeholder="草案 ID" />
          <button class="secondary-action" type="button" :disabled="isLoading" @click="loadPlan">
            <Search :size="16" />{{ isLoading ? "读取中…" : "读取" }}
          </button>
        </div>

        <div v-if="plan" class="plan-summary">
          <header>
            <div><span>草案 #{{ plan.plan_id }}</span><strong>{{ plan.name }}</strong></div>
            <span class="readonly-pill">不可执行</span>
          </header>
          <div class="plan-metrics">
            <div><span>总权益</span><strong>{{ formatDecimal(plan.total_equity) }}</strong></div>
            <div><span>目标现金</span><strong>{{ formatDecimal(plan.target_cash) }}</strong></div>
            <div><span>预计现金</span><strong>{{ formatDecimal(plan.cash_after) }}</strong></div>
          </div>
          <div class="table-panel full">
            <table>
              <thead><tr><th>标的</th><th>原始</th><th>约束后</th><th>最终</th><th>当前/目标/差额</th><th>原因</th></tr></thead>
              <tbody>
                <tr v-for="target in plan.targets" :key="target.symbol">
                  <td>{{ target.symbol }}</td>
                  <td>{{ formatPercent(target.requested_weight) }}</td>
                  <td>{{ formatPercent(target.constrained_weight) }}</td>
                  <td>{{ formatPercent(target.final_weight) }}</td>
                  <td>{{ target.current_quantity }} / {{ target.target_quantity }} / {{ target.order_quantity }}</td>
                  <td>{{ target.reasons.map((reason) => reasonLabels[reason] ?? reason).join("、") || "-" }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="readiness-panel">
            <div class="lookup-row">
              <div>
                <strong>风险前数据核对</strong>
                <span>候选模拟账户，仅作本次只读诊断</span>
              </div>
              <input v-if="accountId === undefined" v-model="paperAccountId" inputmode="numeric" placeholder="模拟账户 ID" />
              <span v-else class="readonly-pill">账户 #{{ accountId }}</span>
              <button
                class="secondary-action"
                type="button"
                :disabled="!writeEnabled || isCheckingReadiness"
                @click="checkReadiness"
              >{{ isCheckingReadiness ? "核对中…" : "重新核对" }}</button>
            </div>
            <p v-if="!writeEnabled" class="inline-error">Demo 模式不提供真实账户核对。</p>
            <div class="lookup-row approval-lookup">
              <div>
                <strong>恢复确认或执行结果</strong>
                <span>响应丢失或页面刷新后，可按确认 ID 回读</span>
              </div>
              <input v-model="approvalLookupId" inputmode="numeric" placeholder="确认 ID" />
              <button class="secondary-action" type="button" :disabled="isLoadingApproval" @click="loadApproval">
                <Search :size="16" />{{ isLoadingApproval ? "读取中…" : "读取确认" }}
              </button>
            </div>
            <div v-if="readiness" class="readiness-result">
              <strong :class="readiness.ready_for_risk_check ? 'readiness-ok' : 'readiness-blocked'">
                {{ readiness.ready_for_risk_check ? "前置数据一致，可进入后续正式风控" : `存在 ${readiness.blockers.length} 个阻断项` }}
              </strong>
              <span>仍未执行风控，且不可创建订单或成交。</span>
              <ul v-if="readiness.blockers.length">
                <li v-for="blocker in readiness.blockers" :key="`${blocker.code}-${blocker.symbol ?? 'all'}`">
                  <code>{{ blocker.code }}</code>
                  <span>{{ blocker.symbol ? `${blocker.symbol} · ` : "" }}{{ blocker.message }}</span>
                </li>
              </ul>
              <div v-if="readiness.ready_for_risk_check" class="risk-actions">
                <button class="secondary-action" type="button" :disabled="isCapturingEquity" @click="captureEquity">
                  {{ isCapturingEquity ? "采集中…" : "采集账户净值快照" }}
                </button>
                <button class="secondary-action" type="button" :disabled="!equitySnapshot || isEvaluatingRisk" @click="evaluateRisk">
                  {{ isEvaluatingRisk ? "评估中…" : "运行正式风控" }}
                </button>
              </div>
              <div v-if="equitySnapshot" class="risk-fact-row">
                <span>净值快照 #{{ equitySnapshot.snapshot_id }}</span>
                <span>观测期 {{ equitySnapshot.observation_count }} 日 · {{ historyStatusLabel(equitySnapshot.history_status) }}</span>
                <span>回撤 {{ formatPercent(equitySnapshot.drawdown_since_monitoring_start) }}</span>
              </div>
              <div v-if="riskEvaluation" class="risk-evaluation-result">
                <strong :class="riskEvaluation.risk_passed ? 'readiness-ok' : 'readiness-blocked'">
                  {{ riskEvaluation.risk_passed ? "风险评估通过" : "风险评估阻断" }}
                </strong>
                <span>检查 #{{ riskEvaluation.check_id }} · 不会创建订单或成交</span>
                <ul>
                  <li v-for="order in riskEvaluation.orders" :key="order.symbol">
                    <code>{{ order.symbol }}</code>
                    <span>{{ order.passed ? "通过" : order.rejection_reasons.join("、") }}</span>
                  </li>
                </ul>
                <div v-if="riskEvaluation.risk_passed" class="approval-panel">
                  <template v-if="!approval">
                    <div class="approval-heading">
                      <ShieldCheck :size="18" aria-hidden="true" />
                      <strong>确认模拟执行意图</strong>
                    </div>
                    <code>{{ expectedConfirmationPhrase }}</code>
                    <input
                      v-model="confirmationPhrase"
                      autocomplete="off"
                      :placeholder="expectedConfirmationPhrase"
                    />
                    <button
                      class="primary-action"
                      type="button"
                      :disabled="!writeEnabled || confirmationPhrase !== expectedConfirmationPhrase || isApproving"
                      @click="confirmPaperExecution"
                    >
                      <ShieldCheck :size="16" />{{ isApproving ? "确认中…" : "记录模拟执行确认" }}
                    </button>
                  </template>
                  <template v-else>
                    <div class="approval-heading">
                      <ShieldCheck :size="18" aria-hidden="true" />
                      <strong>确认 #{{ approval.approval_id }}</strong>
                      <span :class="['status', `status-${displayedApprovalStatus}`]">{{ statusLabel(displayedApprovalStatus ?? "pending") }}</span>
                    </div>
                    <div class="approval-facts">
                      <span>评估 {{ hashFingerprint(approval.evaluation_hash) }}</span>
                      <span>批次 {{ hashFingerprint(approval.order_batch_hash) }}</span>
                      <span>到期 {{ formatDateTime(approval.expires_at) }}</span>
                    </div>
                    <ul class="approval-orders">
                      <li v-for="order in approval.order_summary" :key="order.execution_index">
                        <code>{{ order.execution_index + 1 }} · {{ order.symbol }}</code>
                        <span>
                          {{ order.side === "sell" ? "卖出" : "买入" }}
                          {{ Math.abs(order.signed_quantity) }} · 预计 {{ formatDecimal(order.estimated_fill_price) }}
                          · 费用 {{ formatDecimal(order.estimated_commission) }}
                        </span>
                      </li>
                    </ul>
                    <span v-if="estimatedCashAfter" class="approval-cash">
                      预计执行后现金 {{ formatDecimal(estimatedCashAfter) }}
                    </span>
                    <p class="form-warning">此记录不是执行许可，尚未创建订单或成交。</p>
                    <div v-if="displayedApprovalStatus === 'pending'" class="risk-actions">
                      <button
                        class="primary-action"
                        type="button"
                        :disabled="!writeEnabled || isExecuting"
                        @click="executeApprovedPaperBatch"
                      >
                        <Play :size="16" />{{ isExecuting ? "模拟执行中…" : "执行模拟交易" }}
                      </button>
                      <button
                        class="secondary-action"
                        type="button"
                        :disabled="isRevokingApproval || isExecuting"
                        @click="revokeApproval"
                      >
                        <XCircle :size="16" />{{ isRevokingApproval ? "撤销中…" : "撤销确认" }}
                      </button>
                    </div>
                  </template>
                </div>
                <div v-if="execution" class="execution-result">
                  <strong :class="execution.status === 'completed' ? 'readiness-ok' : 'readiness-blocked'">
                    {{ execution.status === "completed" ? "本地模拟执行完成" : "本地模拟执行已拒绝" }}
                  </strong>
                  <span>批次 #{{ execution.execution_id }} · {{ execution.fill_count }}/{{ execution.order_count }} 笔成交 · 未发送真实订单</span>
                  <span v-if="execution.cash_before !== null && execution.cash_after !== null">
                    现金 {{ formatDecimal(execution.cash_before) }} → {{ formatDecimal(execution.cash_after) }} · 费用 {{ formatDecimal(execution.total_commission) }}
                  </span>
                  <p v-if="execution.failure_message" class="inline-error">{{ execution.failure_code }} · {{ execution.failure_message }}</p>
                  <ul v-if="execution.orders.length">
                    <li v-for="order in execution.orders" :key="order.order_id">
                      <code>{{ order.execution_index + 1 }} · {{ order.symbol }}</code>
                      <span>{{ order.side === "sell" ? "卖出" : "买入" }} {{ order.quantity }} @ {{ formatDecimal(order.fill_price) }}</span>
                    </li>
                  </ul>
                  <p v-if="execution.status === 'completed'" class="form-warning">
                    账户状态已变化，下一步需重新采集净值快照后再进行风控。
                  </p>
                  <p v-else class="form-warning">
                    整批在预演阶段被拒绝，账户、持仓和现金未变化。请修正阻断原因后重新核对、风控并确认。
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="empty-panel">保存新草案或输入 ID 回读。这里不会触发风控、订单或成交。</div>
      </div>
    </div>
  </section>
</template>
