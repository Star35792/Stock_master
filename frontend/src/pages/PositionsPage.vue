<script setup lang="ts">
import { ArrowLeftRight, BookOpen, CircleAlert, Pencil, Plus, RefreshCw, Trash2, Wallet } from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import {
  createPositionBook,
  createPositionTrade,
  deletePositionTrade,
  fetchPositionBookHoldings,
  fetchPositionBooks,
  fetchPositionTrades,
  fetchPortfolioPlanDrift,
  updatePositionBook,
} from "../api";
import type { PortfolioPlanDrift, PositionBook, PositionBookHoldings, PositionTrade } from "../types";
import { formatDate, formatNumber, formatPercent } from "../components/operations/formatters";

const books = ref<PositionBook[]>([]);
const selectedBookId = ref<number | null>(null);
const holdings = ref<PositionBookHoldings | null>(null);
const trades = ref<PositionTrade[]>([]);
const loading = ref(false);
const actionError = ref<string | null>(null);
const notice = ref<string | null>(null);

const newBookName = ref("");
const newBookCash = ref("0");
const creatingBook = ref(false);

const editingBook = ref(false);
const editingBookId = ref<number | null>(null);
const editForm = reactive({ name: "", cash: "", note: "" });
const savingBook = ref(false);

const sortKey = ref<"market_value" | "unrealized_pnl_pct" | "weight">("market_value");
const sortAsc = ref(false);
const industryFilter = ref("");

const sortedFilteredHoldings = computed(() => {
  const rows = [...(holdings.value?.holdings ?? [])];
  const filtered = industryFilter.value
    ? rows.filter((row) => (row.industry ?? "-") === industryFilter.value)
    : rows;
  const key = sortKey.value;
  filtered.sort((a, b) => {
    const left = Number(a[key] ?? 0);
    const right = Number(b[key] ?? 0);
    return sortAsc.value ? left - right : right - left;
  });
  return filtered;
});

const industryOptions = computed(() => {
  const values = new Set<string>();
  for (const row of holdings.value?.holdings ?? []) {
    values.add(row.industry ?? "-");
  }
  return [...values].sort((a, b) => a.localeCompare(b, "zh-CN"));
});

function toggleSort(key: typeof sortKey.value) {
  if (sortKey.value === key) {
    sortAsc.value = !sortAsc.value;
  } else {
    sortKey.value = key;
    sortAsc.value = false;
  }
}

function startEditBook() {
  const book = selectedBook.value;
  if (!book) return;
  editingBookId.value = book.id;
  editForm.name = book.name;
  editForm.cash = book.cash;
  editForm.note = book.note ?? "";
  editingBook.value = true;
}

async function saveBookEdit() {
  const bookId = editingBookId.value;
  if (!editingBook.value || bookId === null || bookId !== selectedBookId.value || savingBook.value) return;
  const name = editForm.name.trim();
  if (!name) {
    actionError.value = "账本名称不能为空。";
    return;
  }
  savingBook.value = true;
  actionError.value = null;
  try {
    await updatePositionBook(bookId, {
      name,
      cash: editForm.cash,
      note: editForm.note || null,
    });
    editingBook.value = false;
    notice.value = "账本已更新。";
    await loadBooks(bookId);
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : "账本更新失败";
  } finally {
    savingBook.value = false;
  }
}

const tradeForm = reactive({
  side: "buy" as "buy" | "sell",
  symbol: "",
  quantity: "100",
  price: "",
  fee: "0",
  trade_date: new Date().toISOString().slice(0, 10),
});
const recordingTrade = ref(false);

const driftPlanId = ref("");
const driftThreshold = ref("0.02");
const drift = ref<PortfolioPlanDrift | null>(null);
const driftLoading = ref(false);

let tradeIdempotencyKey: string | null = null;
let tradeRequestFingerprint: string | null = null;
let bookRequestGeneration = 0;
let driftRequestGeneration = 0;

watch(selectedBookId, () => {
  bookRequestGeneration += 1;
  driftRequestGeneration += 1;
  editingBook.value = false;
  editingBookId.value = null;
  holdings.value = null;
  trades.value = [];
  drift.value = null;
  loading.value = false;
  driftLoading.value = false;
  actionError.value = null;
  notice.value = null;
  industryFilter.value = "";
}, { flush: "sync" });

onBeforeUnmount(() => {
  bookRequestGeneration += 1;
  driftRequestGeneration += 1;
});

const selectedBook = computed(() => books.value.find((book) => book.id === selectedBookId.value) ?? null);

async function loadBooks(preferredId?: number) {
  actionError.value = null;
  try {
    books.value = await fetchPositionBooks();
    if (books.value.length === 0) {
      selectedBookId.value = null;
      holdings.value = null;
      trades.value = [];
      return;
    }
    const nextId = preferredId ?? selectedBookId.value ?? books.value[0].id;
    selectedBookId.value = books.value.some((book) => book.id === nextId) ? nextId : books.value[0].id;
    await refreshBookData();
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : "账本读取失败";
  }
}

async function refreshBookData() {
  const bookId = selectedBookId.value;
  const generation = ++bookRequestGeneration;
  holdings.value = null;
  trades.value = [];
  if (bookId === null) return;
  loading.value = true;
  actionError.value = null;
  try {
    const [holdingsResult, tradesResult] = await Promise.all([
      fetchPositionBookHoldings(bookId),
      fetchPositionTrades(bookId),
    ]);
    if (generation !== bookRequestGeneration || bookId !== selectedBookId.value) return;
    holdings.value = holdingsResult;
    trades.value = tradesResult;
  } catch (error) {
    if (generation !== bookRequestGeneration) return;
    actionError.value = error instanceof Error ? error.message : "持仓读取失败";
  } finally {
    if (generation === bookRequestGeneration) loading.value = false;
  }
}

async function handleCreateBook() {
  const name = newBookName.value.trim();
  if (!name) {
    actionError.value = "请输入账本名称。";
    return;
  }
  creatingBook.value = true;
  actionError.value = null;
  try {
    const book = await createPositionBook({ name, cash: newBookCash.value || "0" });
    newBookName.value = "";
    newBookCash.value = "0";
    notice.value = `账本「${book.name}」已创建。`;
    await loadBooks(book.id);
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : "账本创建失败";
  } finally {
    creatingBook.value = false;
  }
}

async function handleRecordTrade() {
  const bookId = selectedBookId.value;
  if (bookId === null || recordingTrade.value) return;
  const symbol = tradeForm.symbol.trim();
  const quantity = Number(tradeForm.quantity);
  if (!symbol || !Number.isSafeInteger(quantity) || quantity <= 0 || !tradeForm.price) {
    actionError.value = "请完整填写代码、数量（正整数）和价格。";
    return;
  }
  recordingTrade.value = true;
  actionError.value = null;
  const payload = {
    side: tradeForm.side,
    symbol,
    quantity,
    price: tradeForm.price,
    fee: tradeForm.fee || "0",
    trade_date: tradeForm.trade_date,
  };
  const fingerprint = JSON.stringify({ bookId, ...payload });
  if (fingerprint !== tradeRequestFingerprint) {
    tradeIdempotencyKey = null;
    tradeRequestFingerprint = fingerprint;
  }
  try {
    tradeIdempotencyKey = tradeIdempotencyKey ?? crypto.randomUUID();
    await createPositionTrade(
      bookId,
      payload,
      tradeIdempotencyKey,
    );
    tradeIdempotencyKey = null;
    tradeRequestFingerprint = null;
    if (bookId !== selectedBookId.value) return;
    notice.value = `已记录${payload.side === "buy" ? "买入" : "卖出"} ${symbol} ${quantity} 股。`;
    tradeForm.symbol = "";
    tradeForm.price = "";
    await refreshBookData();
  } catch (error) {
    // 响应丢失时提交结果未知；相同账本和请求重试必须继续使用原键。
    if (bookId === selectedBookId.value) {
      actionError.value = error instanceof Error ? error.message : "记账失败";
    }
  } finally {
    recordingTrade.value = false;
  }
}

async function handleDeleteTrade(tradeId: number) {
  if (selectedBookId.value === null) return;
  if (!window.confirm(`确认删除流水 #${tradeId}？持仓将按剩余流水重新推导。`)) return;
  actionError.value = null;
  try {
    await deletePositionTrade(selectedBookId.value, tradeId);
    notice.value = `流水 #${tradeId} 已删除。`;
    await refreshBookData();
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : "流水删除失败";
  }
}

async function handleFetchDrift() {
  const bookId = selectedBookId.value;
  const generation = ++driftRequestGeneration;
  const planId = Number(driftPlanId.value);
  if (!Number.isFinite(planId) || planId <= 0 || bookId === null) {
    actionError.value = "偏离检测需要组合草案 ID 和已选账本。";
    return;
  }
  driftLoading.value = true;
  actionError.value = null;
  try {
    const result = await fetchPortfolioPlanDrift(planId, {
      positionBookId: bookId,
      threshold: driftThreshold.value || "0.02",
    });
    if (generation === driftRequestGeneration) drift.value = result;
  } catch (error) {
    if (generation !== driftRequestGeneration) return;
    drift.value = null;
    actionError.value = error instanceof Error ? error.message : "偏离检测失败";
  } finally {
    if (generation === driftRequestGeneration) driftLoading.value = false;
  }
}

const directionLabels: Record<string, string> = {
  buy: "增持",
  sell: "减持",
  hold: "持有",
};

function pnlTone(value: string | null): string {
  if (value === null) return "neutral";
  return Number(value) > 0 ? "positive" : Number(value) < 0 ? "negative" : "neutral";
}

function maxExposureWeight(): number {
  const rows = holdings.value?.industry_exposure ?? [];
  return rows.reduce((max, row) => Math.max(max, Number(row.weight)), 0);
}

onMounted(() => {
  void loadBooks();
});
</script>

<template>
  <section class="positions-page">
    <header class="section-title">
      <Wallet :size="18" />
      <div>
        <h2>持仓分析</h2>
        <p class="section-note">
          手工记录外部终端的真实买卖流水，按可信行情估值；仅用于持仓管理，不连接券商、不下单。
        </p>
      </div>
    </header>

    <p v-if="actionError" class="inline-error"><CircleAlert :size="14" />{{ actionError }}</p>
    <p v-if="notice" class="inline-notice">{{ notice }}</p>
    <p v-if="holdings?.sequence_anomaly" class="inline-error">
      <CircleAlert :size="14" />
      账本存在乱序流水（卖出早于其覆盖的买入被补录）。重放会把该笔卖出所得全额计入已实现盈亏，请核对流水日期后修正。
    </p>

    <div class="lookup-row book-bar">
      <div class="book-select">
        <label>持仓账本</label>
        <select v-model="selectedBookId" :disabled="books.length === 0 || savingBook || recordingTrade" @change="refreshBookData">
          <option v-if="books.length === 0" :value="null">暂无账本</option>
          <option v-for="book in books" :key="book.id" :value="book.id">
            #{{ book.id }} {{ book.name }}（现金 {{ formatNumber(book.cash) }}）
          </option>
        </select>
      </div>
      <button class="secondary-action" type="button" :disabled="loading || selectedBookId === null" @click="refreshBookData">
        <RefreshCw :size="16" />{{ loading ? "刷新中…" : "刷新" }}
      </button>
      <button class="secondary-action" type="button" :disabled="selectedBookId === null" @click="startEditBook">
        <Pencil :size="15" />编辑
      </button>
      <div class="new-book">
        <input v-model="newBookName" placeholder="新账本名称" />
        <input v-model="newBookCash" inputmode="decimal" placeholder="初始现金" />
        <button type="button" :disabled="creatingBook" @click="handleCreateBook">
          <Plus :size="16" />{{ creatingBook ? "创建中…" : "创建账本" }}
        </button>
      </div>
    </div>

    <div v-if="editingBook" class="exposure-panel book-edit-panel">
      <h3><Pencil :size="15" />编辑账本「{{ selectedBook?.name }}」</h3>
      <div class="trade-form">
        <label>名称<input v-model="editForm.name" maxlength="100" /></label>
        <label>现金<input v-model="editForm.cash" inputmode="decimal" /></label>
        <label>备注<input v-model="editForm.note" maxlength="200" placeholder="可选" /></label>
        <button type="button" :disabled="savingBook" @click="saveBookEdit">
          {{ savingBook ? "保存中…" : "保存" }}
        </button>
        <button class="secondary-action" type="button" @click="editingBook = false">取消</button>
      </div>
    </div>

    <div v-if="holdings" class="summary-grid">
      <div class="metric-card"><span>总权益</span><strong>{{ formatNumber(holdings.total_equity) }}</strong><small>现金 {{ formatNumber(holdings.cash) }} + 持仓 {{ formatNumber(holdings.positions_market_value) }}</small></div>
      <div class="metric-card"><span>已实现盈亏</span><strong :class="pnlTone(holdings.realized_pnl)">{{ formatNumber(holdings.realized_pnl) }}</strong><small>流水重放推导</small></div>
      <div class="metric-card"><span>集中度 HHI</span><strong>{{ formatPercent(holdings.concentration.hhi) }}</strong><small>Top5 {{ formatPercent(holdings.concentration.top5_weight) }} · 最大单票 {{ formatPercent(holdings.concentration.max_symbol_weight) }}</small></div>
      <div class="metric-card" :class="{ warning: holdings.price_stale }">
        <span>估值基准</span>
        <strong>{{ holdings.price_as_of ? formatDate(holdings.price_as_of) : "无可信价" }}</strong>
        <small>{{ holdings.price_stale ? "存在缺价持仓，按成本估值" : "最新可信未复权收盘价" }}</small>
      </div>
    </div>

    <div v-if="holdings && holdings.holdings.length > 0" class="table-panel full">
      <div class="table-toolbar">
        <label class="industry-filter">行业
          <select v-model="industryFilter">
            <option value="">全部（{{ holdings.holdings.length }} 只）</option>
            <option v-for="industry in industryOptions" :key="industry" :value="industry">
              {{ industry }}（{{ holdings!.holdings.filter((row) => (row.industry ?? "-") === industry).length }}）
            </option>
          </select>
        </label>
      </div>
      <table>
        <thead>
          <tr>
            <th>代码</th><th>名称</th><th>行业</th><th>数量</th><th>成本</th>
            <th>现价</th>
            <th class="sortable" @click="toggleSort('market_value')">市值 {{ sortKey === "market_value" ? (sortAsc ? "↑" : "↓") : "" }}</th>
            <th>浮盈</th>
            <th class="sortable" @click="toggleSort('unrealized_pnl_pct')">收益率 {{ sortKey === "unrealized_pnl_pct" ? (sortAsc ? "↑" : "↓") : "" }}</th>
            <th class="sortable" @click="toggleSort('weight')">权重 {{ sortKey === "weight" ? (sortAsc ? "↑" : "↓") : "" }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in sortedFilteredHoldings" :key="row.symbol">
            <td>{{ row.symbol }}</td>
            <td>{{ row.name ?? "-" }}</td>
            <td>{{ row.industry ?? "-" }}</td>
            <td>{{ formatNumber(row.quantity) }}</td>
            <td>{{ formatNumber(row.average_cost) }}</td>
            <td>
              {{ row.price ? formatNumber(row.price) : "-" }}
              <small v-if="row.price_stale" class="stale-tag">缺价</small>
              <small v-if="row.sequence_anomaly" class="stale-tag">乱序</small>
            </td>
            <td>{{ formatNumber(row.market_value) }}</td>
            <td :class="pnlTone(row.unrealized_pnl)">{{ row.unrealized_pnl === null ? "-" : formatNumber(row.unrealized_pnl) }}</td>
            <td :class="pnlTone(row.unrealized_pnl_pct)">{{ row.unrealized_pnl_pct === null ? "-" : formatPercent(row.unrealized_pnl_pct) }}</td>
            <td>{{ formatPercent(row.weight) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else-if="holdings && !loading" class="empty-hint">当前账本暂无持仓，先在下方记一笔买入流水。</p>

    <div v-if="holdings && holdings.industry_exposure.length > 0" class="exposure-panel">
      <h3><BookOpen :size="15" />行业暴露</h3>
      <div v-for="row in holdings.industry_exposure" :key="row.industry" class="exposure-row">
        <span class="exposure-label">{{ row.industry }}（{{ row.symbol_count }} 只）</span>
        <div class="exposure-bar">
          <div
            class="exposure-fill"
            :style="{ width: `${maxExposureWeight() > 0 ? (Number(row.weight) / maxExposureWeight()) * 100 : 0}%` }"
          />
        </div>
        <span class="exposure-value">{{ formatPercent(row.weight) }}</span>
      </div>
    </div>

    <div class="trade-panel">
      <h3><ArrowLeftRight :size="15" />记一笔交易</h3>
      <div class="trade-form">
        <select v-model="tradeForm.side">
          <option value="buy">买入</option>
          <option value="sell">卖出</option>
        </select>
        <input v-model="tradeForm.symbol" placeholder="证券代码，如 600000.SH" />
        <input v-model="tradeForm.quantity" inputmode="numeric" placeholder="数量" />
        <input v-model="tradeForm.price" inputmode="decimal" placeholder="成交价" />
        <input v-model="tradeForm.fee" inputmode="decimal" placeholder="费用" />
        <input v-model="tradeForm.trade_date" type="date" />
        <button
          type="button"
          :disabled="recordingTrade || selectedBookId === null"
          @click="handleRecordTrade"
        >{{ recordingTrade ? "记录中…" : "记录流水" }}</button>
      </div>
      <p class="section-note">卖出前会校验净持仓；删除流水会重新推导持仓与已实现盈亏。</p>
    </div>

    <div v-if="trades.length > 0" class="table-panel full">
      <table>
        <thead>
          <tr><th>日期</th><th>方向</th><th>代码</th><th>数量</th><th>价格</th><th>费用</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="trade in trades" :key="trade.id">
            <td>{{ formatDate(trade.trade_date) }}</td>
            <td :class="trade.side === 'buy' ? 'positive' : 'negative'">{{ trade.side === "buy" ? "买入" : "卖出" }}</td>
            <td>{{ trade.symbol }}</td>
            <td>{{ formatNumber(trade.quantity) }}</td>
            <td>{{ formatNumber(trade.price) }}</td>
            <td>{{ formatNumber(trade.fee) }}</td>
            <td>
              <button class="icon-action" type="button" :title="'删除流水 #' + trade.id" @click="handleDeleteTrade(trade.id)">
                <Trash2 :size="14" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="drift-panel">
      <h3><ArrowLeftRight :size="15" />组合草案偏离检测</h3>
      <div class="lookup-row">
        <input v-model="driftPlanId" inputmode="numeric" placeholder="组合草案 ID" />
        <input v-model="driftThreshold" inputmode="decimal" placeholder="阈值，默认 0.02" />
        <button class="secondary-action" type="button" :disabled="driftLoading" @click="handleFetchDrift">
          {{ driftLoading ? "检测中…" : "检测偏离" }}
        </button>
      </div>
      <p class="section-note">把草案最终权重与当前账本实仓权重对比，只读建议，不生成订单。</p>
      <div v-if="drift" class="drift-result">
        <div class="plan-metrics">
          <div><span>偏离超阈值</span><strong>{{ drift.summary.off_threshold_count }} / {{ drift.rows.length }}</strong></div>
          <div><span>最大偏离</span><strong>{{ formatPercent(drift.summary.max_abs_drift) }}</strong></div>
          <div><span>现金权重</span><strong>{{ formatPercent(drift.summary.cash_weight) }} / 目标 {{ formatPercent(drift.summary.cash_target_weight) }}</strong></div>
          <div><span>估值基准</span><strong>{{ drift.price_as_of ? formatDate(drift.price_as_of) : "缺价" }}</strong></div>
        </div>
        <div class="table-panel full">
          <table>
            <thead>
              <tr><th>代码</th><th>目标权重</th><th>当前权重</th><th>偏离</th><th>方向</th><th>当前/目标数量</th><th>原因</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in drift.rows" :key="row.symbol">
                <td>{{ row.symbol }}</td>
                <td>{{ formatPercent(row.target_weight) }}</td>
                <td>{{ formatPercent(row.current_weight) }}</td>
                <td :class="pnlTone(row.drift)">{{ formatPercent(row.drift) }}</td>
                <td :class="row.direction === 'buy' ? 'positive' : row.direction === 'sell' ? 'negative' : ''">
                  {{ directionLabels[row.direction] }}
                </td>
                <td>{{ formatNumber(row.current_quantity) }} / {{ row.target_quantity === null ? "-" : formatNumber(row.target_quantity) }}</td>
                <td>{{ row.reasons.join("、") }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.positions-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.inline-notice {
  margin: 0;
  padding: 8px 12px;
  border-radius: 8px;
  background: #ecfdf5;
  color: #047857;
  font-size: 13px;
}

.book-bar {
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-end;
}

.book-select {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.book-select label {
  font-size: 12px;
  color: var(--text-muted, #64748b);
}

.new-book {
  display: flex;
  gap: 8px;
  margin-left: auto;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}

.positive {
  color: #dc2626;
}

.negative {
  color: #16a34a;
}

.stale-tag {
  margin-left: 4px;
  padding: 1px 6px;
  border-radius: 8px;
  background: #fef3c7;
  color: #92400e;
  font-size: 11px;
}

.empty-hint {
  color: var(--text-muted, #64748b);
  font-size: 13px;
}

.exposure-panel,
.trade-panel,
.drift-panel {
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 10px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: var(--panel-bg, #fff);
}

.exposure-panel h3,
.trade-panel h3,
.drift-panel h3 {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  margin: 0;
}

.exposure-row {
  display: grid;
  grid-template-columns: 140px 1fr 70px;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.exposure-bar {
  height: 10px;
  border-radius: 5px;
  background: #eef2f7;
  overflow: hidden;
}

.exposure-fill {
  height: 100%;
  background: #2563eb;
}

.trade-form {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.drift-result {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.icon-action {
  border: none;
  background: transparent;
  color: #dc2626;
  cursor: pointer;
  padding: 4px;
}
</style>
<style scoped>
.book-edit-panel label {
  display: grid;
  gap: 4px;
  font-size: 12px;
  color: #64748b;
}

.table-toolbar {
  display: flex;
  justify-content: flex-end;
  padding: 8px 10px 0;
}

.industry-filter {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #64748b;
}

.industry-filter select {
  min-height: 32px;
}

.sortable {
  cursor: pointer;
  user-select: none;
}

.sortable:hover {
  color: #2563eb;
}
</style>
