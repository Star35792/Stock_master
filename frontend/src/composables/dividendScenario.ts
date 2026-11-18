import { reactive, ref, watch } from "vue";
import { evaluateDividendScenario, type DividendScenarioInput, type DividendScenarioReport } from "../dividendScenarioApi";

export function dividendSample(): DividendScenarioInput {
  return {
    input_kind: "synthetic", price_basis: "unadjusted_synthetic", symbol: "600000.SH",
    initial_cash: "1000", initial_quantity: 100, initial_price: "10",
    prices: [
      { trade_date: "2026-01-05", close: "10" }, { trade_date: "2026-01-06", close: "9.80" },
      { trade_date: "2026-01-07", close: "9.80" }, { trade_date: "2026-01-08", close: "9.80" },
    ],
    events: [{ event_id: "scenario-dividend-1", revision: "v1", action_type: "cash_dividend",
      record_date: "2026-01-05", ex_date: "2026-01-06", payment_date: "2026-01-08",
      cash_per_share: "0.20", assumed_tax_rate: "0" }], fills: [],
  };
}

export function createDividendScenario(deps = { evaluate: evaluateDividendScenario }) {
  const input = reactive<DividendScenarioInput>(dividendSample());
  const report = ref<DividendScenarioReport | null>(null);
  const loading = ref(false);
  const error = ref("");
  let generation = 0;
  let controller: AbortController | null = null;
  function invalidate() {
    generation++;
    controller?.abort();
    controller = null;
    report.value = null;
    loading.value = false;
    error.value = "";
  }
  const stopWatch = watch(input, invalidate, { deep: true, flush: "sync" });
  function sample(kind: "hold" | "sell" | "pending" | "tax") {
    Object.assign(input, dividendSample());
    if (kind === "sell") input.fills.push({ trade_date: "2026-01-07", side: "sell", quantity: 100, price: "9.80", fee: "0" });
    if (kind === "pending") input.events[0]!.payment_date = "2026-01-09";
    if (kind === "tax") input.events[0]!.assumed_tax_rate = "0.20";
  }
  async function calculate() {
    if (loading.value) return;
    invalidate();
    const request = generation;
    controller = new AbortController();
    const signal = controller.signal;
    const payload = JSON.parse(JSON.stringify(input)) as DividendScenarioInput;
    loading.value = true;
    try {
      const result = await deps.evaluate(payload, signal);
      if (request === generation && !signal.aborted) report.value = result;
    } catch (cause) {
      if (request === generation && !signal.aborted) error.value = cause instanceof Error ? cause.message : "情景计算失败";
    } finally {
      if (request === generation) { loading.value = false; controller = null; }
    }
  }
  function dispose() { invalidate(); stopWatch(); }
  return { input, report, loading, error, calculate, sample, dispose };
}
