import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";

import {
  createPositionBook,
  createPositionTrade,
  deletePositionTrade,
  fetchPositionBookHoldings,
  fetchPositionBooks,
  fetchPositionTrades,
  fetchPortfolioPlanDrift,
  fetchPortfolioPlans,
  PortfolioDriftApiError,
  updatePositionBook,
} from "../api";
import type { PortfolioPlanDrift, PortfolioPlanSummary, PositionBook, PositionBookHoldings, PositionTrade } from "../types";
import { shanghaiDate } from "../components/operations/formatters";

/**
 * 持仓账本工作流：账本增删改、流水记账、持仓/流水刷新、
 * 组合草案偏离检测与草案下拉选项加载。逻辑与视图分离，外部依赖可注入。
 */
export interface PositionBookWorkspaceDeps {
  createPositionBook: typeof createPositionBook;
  createPositionTrade: typeof createPositionTrade;
  deletePositionTrade: typeof deletePositionTrade;
  fetchPositionBookHoldings: typeof fetchPositionBookHoldings;
  fetchPositionBooks: typeof fetchPositionBooks;
  fetchPositionTrades: typeof fetchPositionTrades;
  fetchPortfolioPlanDrift: typeof fetchPortfolioPlanDrift;
  fetchPortfolioPlans: typeof fetchPortfolioPlans;
  updatePositionBook: typeof updatePositionBook;
  PortfolioDriftApiError: typeof PortfolioDriftApiError;
  shanghaiDate: () => string;
}

export interface PositionBookWorkspaceOptions {
  /** 危险操作确认（删除流水），默认 window.confirm。 */
  confirm?: (message: string) => boolean;
}

const defaultDeps: PositionBookWorkspaceDeps = {
  createPositionBook,
  createPositionTrade,
  deletePositionTrade,
  fetchPositionBookHoldings,
  fetchPositionBooks,
  fetchPositionTrades,
  fetchPortfolioPlanDrift,
  fetchPortfolioPlans,
  updatePositionBook,
  PortfolioDriftApiError,
  shanghaiDate,
};

export function createPositionBookWorkspace(
  deps: PositionBookWorkspaceDeps = defaultDeps,
  options: PositionBookWorkspaceOptions = {},
) {
  const confirm = options.confirm ?? ((message: string) => window.confirm(message));

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
      invalidateDrift();
      await deps.updatePositionBook(bookId, {
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
    trade_date: deps.shanghaiDate(),
  });
  const recordingTrade = ref(false);

  const driftPlanId = ref("");
  const driftThreshold = ref("0.02");
  const planOptions = ref<PortfolioPlanSummary[]>([]);
  const planTotal = ref(0);
  const planOffset = ref(0);
  const planOptionsLoading = ref(false);
  const planOptionsLoaded = ref(false);
  const planOptionsError = ref<string | null>(null);
  const selectedPlan = computed(() =>
    planOptions.value.find(plan => String(plan.plan_id) === driftPlanId.value) ?? null,
  );
  const drift = ref<PortfolioPlanDrift | null>(null);
  const driftLoading = ref(false);
  const driftOutsidePlanCount = computed(() => drift.value?.rows.filter(row => !row.in_plan).length ?? 0);
  const driftEarlierQuoteCount = computed(() => {
    const report = drift.value;
    const latest = report?.price_as_of;
    if (!report || !latest) return 0;
    return report.rows.filter(row => row.price_as_of !== null && row.price_as_of < latest).length;
  });

  let tradeIdempotencyKey: string | null = null;
  let tradeRequestFingerprint: string | null = null;
  let bookRequestGeneration = 0;
  let driftRequestGeneration = 0;
  let planRequestGeneration = 0;

  async function loadPlanOptions(reset = false) {
    if (planOptionsLoading.value) return;
    const generation = ++planRequestGeneration;
    const offset = reset ? 0 : planOffset.value;
    planOptionsLoading.value = true;
    planOptionsError.value = null;
    try {
      const page = await deps.fetchPortfolioPlans(50, offset);
      if (generation !== planRequestGeneration) return;
      if (reset) {
        planOptions.value = page.items;
      } else {
        const known = new Set(planOptions.value.map(plan => plan.plan_id));
        planOptions.value = [
          ...planOptions.value,
          ...page.items.filter(plan => !known.has(plan.plan_id)),
        ];
      }
      planOffset.value = offset + page.items.length;
      planTotal.value = page.total;
      planOptionsLoaded.value = true;
    } catch (error) {
      if (generation !== planRequestGeneration) return;
      planOptionsError.value = error instanceof Error ? error.message : "组合草案列表读取失败";
    } finally {
      if (generation === planRequestGeneration) planOptionsLoading.value = false;
    }
  }

  function invalidateDrift() {
    driftRequestGeneration += 1;
    drift.value = null;
    driftLoading.value = false;
  }

  watch(selectedBookId, () => {
    bookRequestGeneration += 1;
    invalidateDrift();
    editingBook.value = false;
    editingBookId.value = null;
    holdings.value = null;
    trades.value = [];
    loading.value = false;
    actionError.value = null;
    notice.value = null;
    industryFilter.value = "";
  }, { flush: "sync" });

  watch([driftPlanId, driftThreshold], invalidateDrift, { flush: "sync" });

  onBeforeUnmount(() => {
    bookRequestGeneration += 1;
    driftRequestGeneration += 1;
    planRequestGeneration += 1;
  });

  const selectedBook = computed(() => books.value.find((book) => book.id === selectedBookId.value) ?? null);

  async function loadBooks(preferredId?: number) {
    actionError.value = null;
    try {
      books.value = await deps.fetchPositionBooks();
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
    invalidateDrift();
    holdings.value = null;
    trades.value = [];
    if (bookId === null) return;
    loading.value = true;
    actionError.value = null;
    try {
      const [holdingsResult, tradesResult] = await Promise.all([
        deps.fetchPositionBookHoldings(bookId),
        deps.fetchPositionTrades(bookId),
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
      const book = await deps.createPositionBook({ name, cash: newBookCash.value || "0" });
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
      invalidateDrift();
      await deps.createPositionTrade(
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
    if (!confirm(`确认删除流水 #${tradeId}？持仓将按剩余流水重新推导。`)) return;
    actionError.value = null;
    try {
      invalidateDrift();
      await deps.deletePositionTrade(selectedBookId.value, tradeId);
      notice.value = `流水 #${tradeId} 已删除。`;
      await refreshBookData();
    } catch (error) {
      actionError.value = error instanceof Error ? error.message : "流水删除失败";
    }
  }

  async function handleFetchDrift() {
    const bookId = selectedBookId.value;
    invalidateDrift();
    const generation = driftRequestGeneration;
    const planId = Number(driftPlanId.value);
    if (!Number.isFinite(planId) || planId <= 0 || bookId === null) {
      actionError.value = "偏离检测需要组合草案 ID 和已选账本。";
      return;
    }
    driftLoading.value = true;
    actionError.value = null;
    try {
      const result = await deps.fetchPortfolioPlanDrift(planId, {
        positionBookId: bookId,
        threshold: driftThreshold.value || "0.02",
      });
      if (generation === driftRequestGeneration) drift.value = result;
    } catch (error) {
      if (generation !== driftRequestGeneration) return;
      drift.value = null;
      if (
        error instanceof deps.PortfolioDriftApiError
        && error.code === "portfolio_drift_missing_trusted_prices"
        && error.symbols.length > 0
      ) {
        actionError.value = `以下非零持仓缺少可信价格，暂不能计算组合偏离：${error.symbols.join("、")}。请先补齐或修复可信行情后重试。`;
      } else {
        actionError.value = error instanceof Error ? error.message : "偏离检测失败";
      }
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

  const quoteCoverage = computed(() => {
    const rows = holdings.value?.holdings ?? [];
    const latest = holdings.value?.price_as_of ?? null;
    return {
      total: rows.length,
      priced: rows.filter(row => row.price !== null && row.price_as_of !== null && !row.price_stale).length,
      missing: rows.filter(row => row.price_stale || row.price === null).length,
      earlier: rows.filter(row => row.price_as_of !== null && latest !== null && row.price_as_of < latest).length,
    };
  });

  function start() {
    void loadBooks();
    void loadPlanOptions(true);
  }

  return {
    books,
    selectedBookId,
    selectedBook,
    holdings,
    trades,
    loading,
    actionError,
    notice,
    newBookName,
    newBookCash,
    creatingBook,
    editingBook,
    editingBookId,
    editForm,
    savingBook,
    sortKey,
    sortAsc,
    industryFilter,
    sortedFilteredHoldings,
    industryOptions,
    tradeForm,
    recordingTrade,
    driftPlanId,
    driftThreshold,
    planOptions,
    planTotal,
    planOffset,
    planOptionsLoading,
    planOptionsLoaded,
    planOptionsError,
    selectedPlan,
    drift,
    driftLoading,
    driftOutsidePlanCount,
    driftEarlierQuoteCount,
    quoteCoverage,
    directionLabels,
    toggleSort,
    startEditBook,
    saveBookEdit,
    loadPlanOptions,
    loadBooks,
    refreshBookData,
    handleCreateBook,
    handleRecordTrade,
    handleDeleteTrade,
    handleFetchDrift,
    pnlTone,
    maxExposureWeight,
    start,
  };
}

export type PositionBookWorkspace = ReturnType<typeof createPositionBookWorkspace>;
