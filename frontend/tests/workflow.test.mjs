import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc';
import ts from 'typescript';
import * as vue from 'vue';

import { fetchPortfolioPlanDrift, PortfolioDriftApiError } from '../src/api.ts';
import { curvePolyline, normalizeEquity, sameEquityDates } from '../src/components/operations/comparisonCurve.ts';
import { shanghaiDate } from '../src/components/operations/formatters.ts';

const require = createRequire(import.meta.url);

// Compile the actual SFC setup and run it against controlled API responses.
// No DOM or new test dependency is needed to exercise reactive workflow state.
function setup(relativePath, api = {}, props = {}) {
  const filename = new URL(`../src/${relativePath}`, import.meta.url);
  const { descriptor } = parse(readFileSync(filename, 'utf8'));
  const compiled = compileScript(descriptor, { id: relativePath });
  const { outputText } = ts.transpileModule(compiled.content, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const module = { exports: {} };
  const dependencies = new Proxy(api, { get: (target, key) => target[key] ?? (() => {}) });
  const resolve = (name) => {
    if (name === 'vue') return { ...vue, onMounted() {}, onUnmounted() {}, onBeforeUnmount() {} };
    if (name === 'vue-router') return {
      useRoute: api.useRoute ?? (() => ({ name: 'overview', query: {} })),
      useRouter: api.useRouter ?? (() => ({ replace: async () => {} })),
    };
    if (name.endsWith('/comparisonCurve')) return { curvePolyline, normalizeEquity, sameEquityDates };
    if (name.endsWith('/api')) return dependencies;
    if (name.startsWith('.') || name === '@lucide/vue') return dependencies;
    return require(name);
  };
  new Function('require', 'module', 'exports', outputText)(resolve, module, module.exports);
  return module.exports.default.setup(props, { expose() {}, emit() {} });
}

function setupModule(relativePath, api = {}) {
  const filename = new URL(`../src/${relativePath}`, import.meta.url);
  const { outputText } = ts.transpileModule(readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const module = { exports: {} };
  const dependencies = new Proxy(api, { get: (target, key) => target[key] ?? (() => {}) });
  const resolve = (name) => {
    if (name === 'vue') return vue;
    if (name.endsWith('/api')) return dependencies;
    if (name.startsWith('.') || name === '@lucide/vue') return dependencies;
    return require(name);
  };
  new Function('require', 'module', 'exports', outputText)(resolve, module, module.exports);
  return module.exports;
}

function dashboard(api = {}) {
  const { createOperationsDashboard } = setupModule('composables/operationsDashboard.ts');
  const merged = {
    demoOverview: { generated_at: '2026-07-20T05:30:00Z' },
    demoAlerts: { generated_at: '2026-07-20T05:30:00Z', items: [] },
    demoFailureReport: { generated_at: '2026-07-20T05:30:00Z' },
    demoFailureDetails: {},
    demoRerunPlans: {},
    ...api,
  };
  return createOperationsDashboard({
    fetchOverview: merged.fetchOperationsOverview,
    fetchAlerts: merged.fetchOperationsAlerts,
    fetchFailures: merged.fetchOperationsFailures,
    fetchDetail: merged.fetchOperationsFailureDetail ?? (async () => null),
    fetchRerunPlan: merged.fetchOperationsFailureRerunPlan ?? (async () => null),
    demo: {
      overview: merged.demoOverview,
      alerts: merged.demoAlerts,
      failureReport: merged.demoFailureReport,
      failureDetails: merged.demoFailureDetails,
      rerunPlans: merged.demoRerunPlans,
    },
  });
}

function researchWorkbench(api = {}) {
  const { createResearchWorkbench } = setupModule('composables/researchWorkbench.ts');
  return createResearchWorkbench({
    fetchWorkbench: api.fetchResearchWorkbench ?? (async () => ({
      recent_backtests: [], recent_backtests_error: null,
      portfolio_plans: [], portfolio_plans_error: null,
      position_books: [], position_books_error: null,
      alerts: { generated_at: '2026-11-18T06:00:00Z', items: [] }, alerts_error: null,
    })),
    fetchPipeline: api.fetchResearchPipeline ?? (async runId => ({
      run: { id: runId, name: `run-${runId}`, status: 'success' },
      equity_curve: [], equity_curve_error: null,
      performance: null, performance_error: null,
      benchmark: null, benchmark_error: null,
      final_holdings: null, final_holdings_error: null,
    })),
  });
}

function operationsActions(api = {}) {
  const { createOperationsActions } = setupModule('composables/operationsActions.ts');
  return createOperationsActions({
    preview: api.preview ?? (async (actionType, parameters) => ({
      action_type: actionType, executable: true, summary: 'ready', parameters,
      warnings: [], checks: {},
    })),
    start: api.start ?? (async (actionType, parameters) => ({
      action_id: 9, action_type: actionType, status: 'success', phase: 'completed',
      parameters, result: { problem_closed: true }, error_message: null,
      created_at: '2026-11-18T00:00:00Z', started_at: '2026-11-18T00:00:00Z',
      finished_at: '2026-11-18T00:00:01Z',
    })),
    fetch: api.fetch ?? (async () => { throw new Error('unexpected poll'); }),
  });
}

test('operations action previews rerun parameters before starting an audited task', async () => {
  const calls = [];
  const actions = operationsActions({
    preview: async (actionType, parameters) => {
      calls.push(['preview', actionType, parameters]);
      return { action_type: actionType, executable: true, summary: '1 row', parameters,
        warnings: [], checks: { actionable: 1 } };
    },
    start: async (actionType, parameters, key) => {
      calls.push(['start', actionType, parameters, key]);
      return { action_id: 11, action_type: actionType, status: 'success', phase: 'completed',
        parameters, result: { problem_closed: true }, error_message: null,
        created_at: '2026-11-18T00:00:00Z', started_at: '2026-11-18T00:00:00Z',
        finished_at: '2026-11-18T00:00:01Z' };
    },
  });
  const plan = {
    supported: true, action_type: 'daily_bar_gap_backfill',
    parameters: { issue_id: 7, symbols: ['600000.SH'], start_date: '2026-11-17', end_date: '2026-11-17', adjust_type: 'qfq' },
  };

  await actions.executePlan(plan);

  assert.equal(calls[0][0], 'preview');
  assert.equal(calls[1][0], 'start');
  assert.match(calls[1][3], /^frontend-daily_bar_gap_backfill-/);
  assert.equal(actions.currentAction.value.result.problem_closed, true);
});

test('research workbench preserves successful sections when one aggregate section fails', async () => {
  const page = researchWorkbench({
    fetchResearchWorkbench: async () => ({
      recent_backtests: [{ id: 41, name: 'latest', status: 'success' }],
      recent_backtests_error: null,
      portfolio_plans: null,
      portfolio_plans_error: 'portfolio unavailable',
      position_books: [{ id: 3, name: 'manual book', cash: '1000' }],
      position_books_error: null,
      alerts: { generated_at: '2026-11-18T06:00:00Z', items: [] },
      alerts_error: null,
    }),
  });
  await page.loadWorkbench();
  assert.equal(page.source.value, 'partial');
  assert.deepEqual(page.sectionErrors.value, ['组合草案']);
  assert.equal(page.workbench.value.recent_backtests[0].id, 41);
  assert.equal(page.workbench.value.position_books[0].name, 'manual book');
});

test('research pipeline URL changes cannot be overwritten by an older run response', async () => {
  const oldRun = deferred();
  const page = researchWorkbench({
    fetchResearchPipeline: async runId => {
      if (runId === 10) return oldRun.promise;
      return {
        run: { id: runId, name: 'current', status: 'success' },
        equity_curve: [], equity_curve_error: null,
        performance: null, performance_error: null,
        benchmark: null, benchmark_error: null,
        final_holdings: null, final_holdings_error: null,
      };
    },
  });
  const stale = page.loadPipeline(10);
  await page.loadPipeline(11);
  oldRun.resolve({
    run: { id: 10, name: 'stale', status: 'success' },
    equity_curve: [], equity_curve_error: null,
    performance: null, performance_error: null,
    benchmark: null, benchmark_error: null,
    final_holdings: null, final_holdings_error: null,
  });
  await stale;
  assert.equal(page.selectedRunId.value, 11);
  assert.equal(page.pipeline.value.run.id, 11);
  assert.equal(page.pipelineLoading.value, false);
});

test('research pipeline rejects invalid run identifiers before calling the backend', async () => {
  let calls = 0;
  const page = researchWorkbench({ fetchResearchPipeline: async () => { calls++; } });
  await page.loadPipeline(Number.NaN);
  assert.equal(calls, 0);
  assert.match(page.pipelineError.value, /正整数/);
  assert.equal(page.selectedRunId.value, null);
});

test('a failed operations section stays unavailable while successful sections remain live', async () => {
  const liveOverview = { generated_at: '2026-11-16T02:00:00Z' };
  const liveFailures = { generated_at: '2026-11-16T02:01:00Z' };
  const page = dashboard({
    fetchOperationsOverview: async () => liveOverview,
    fetchOperationsAlerts: async () => { throw new Error('alerts unavailable'); },
    fetchOperationsFailures: async () => liveFailures,
  });

  assert.equal(page.source.value, 'loading');
  assert.equal(page.displayOverview.value, null);

  await page.loadDashboard();

  assert.equal(page.source.value, 'partial');
  assert.deepEqual(page.displayOverview.value, liveOverview);
  assert.equal(page.displayAlerts.value, null);
  assert.deepEqual(page.displayFailures.value, liveFailures);
  assert.match(page.errorMessage.value, /告警暂不可用/);
});

test('missing overview is shown as unknown instead of zero or demo counts', async () => {
  const page = dashboard({
    fetchOperationsOverview: async () => { throw new Error('overview unavailable'); },
    fetchOperationsAlerts: async () => ({ generated_at: '2026-11-16T02:00:00Z', items: [{ severity: 'critical' }] }),
    fetchOperationsFailures: async () => ({ generated_at: '2026-11-16T02:00:00Z' }),
  });

  await page.loadDashboard();

  assert.equal(page.source.value, 'partial');
  assert.equal(page.displayOverview.value, null);
  assert.equal(page.snapshotCards.value[0].value, null);
  assert.equal(page.snapshotCards.value[2].value, 1);
});

test('dashboard recovery clears old demo failure details', async () => {
  let available = false;
  const liveOverview = { generated_at: '2026-11-16T02:00:00Z' };
  const liveAlerts = { generated_at: '2026-11-16T02:00:00Z', items: [] };
  const liveFailures = { generated_at: '2026-11-16T02:00:00Z' };
  const request = (value) => async () => {
    if (!available) throw new Error('offline');
    return value;
  };
  const page = dashboard({
    fetchOperationsOverview: request(liveOverview),
    fetchOperationsAlerts: request(liveAlerts),
    fetchOperationsFailures: request(liveFailures),
  });

  await page.loadDashboard();
  assert.equal(page.source.value, 'demo');
  page.selectedFailure.value = { type: 'job', id: 7, label: 'old demo job' };
  page.failureDetail.value = { failure_type: 'job', job: { id: 7 } };
  page.rerunPlan.value = { failure_type: 'job', failure_id: 7 };

  available = true;
  await page.loadDashboard();

  assert.equal(page.source.value, 'api');
  assert.equal(page.selectedFailure.value, null);
  assert.equal(page.displayFailureDetail.value, null);
  assert.equal(page.displayRerunPlan.value, null);
  assert.deepEqual(page.displayOverview.value, liveOverview);
});

test('portfolio default date follows Shanghai calendar across UTC midnight', () => {
  assert.equal(shanghaiDate(new Date('2026-11-15T15:59:00Z')), '2026-11-15');
  assert.equal(shanghaiDate(new Date('2026-11-15T16:01:00Z')), '2026-11-16');
});

function portfolioPlan(api = {}, options = {}) {
  const { createPortfolioPlanWorkflow } = setupModule('composables/portfolioPlan.ts');
  const depNames = [
    'createPortfolioPlan', 'createPaperExecutionApproval', 'capturePaperEquitySnapshot',
    'createRiskEvaluation', 'fetchPortfolioPlan', 'fetchPortfolioRiskReadiness',
    'fetchPaperExecutionApproval', 'fetchPaperExecutionByApproval', 'fetchBacktestFinalHoldings',
    'fetchPositionBookHoldings', 'fetchPositionBooks', 'executePaperApproval',
    'revokePaperExecutionApproval',
  ];
  const deps = Object.fromEntries(depNames.map(name => [
    name,
    api[name] ?? (async () => { throw new Error(`unmocked dep: ${name}`); }),
  ]));
  deps.shanghaiDate = api.shanghaiDate ?? (() => '2026-11-16');
  return createPortfolioPlanWorkflow(deps, options);
}

test('portfolio draft write does not depend on operations overview status', async () => {
  let writes = 0;
  const page = portfolioPlan({
    shanghaiDate: () => '2026-11-16',
    createPortfolioPlan: async () => { writes += 1; return { plan_id: 42 }; },
  });

  assert.equal(page.form.as_of_date, '2026-11-16');
  await page.submitPlan();
  assert.equal(writes, 1);
  assert.equal(page.lookupId.value, '42');
});

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function bookPage(api = {}) {
  const { createPositionBookWorkspace } = setupModule('composables/positionBook.ts');
  const names = [
    'createPositionBook', 'createPositionTrade', 'deletePositionTrade',
    'fetchPositionBookHoldings', 'fetchPositionBooks', 'fetchPositionTrades',
    'fetchPortfolioPlanDrift', 'fetchPortfolioPlans', 'updatePositionBook',
  ];
  const deps = Object.fromEntries(names.map(name => [name, api[name] ?? (async () => undefined)]));
  deps.PortfolioDriftApiError = api.PortfolioDriftApiError ?? (class extends Error {});
  deps.shanghaiDate = api.shanghaiDate ?? (() => '2026-11-18');
  const page = createPositionBookWorkspace(deps);
  page.books.value = [
    { id: 1, name: 'A', cash: '123.4500', note: 'A note' },
    { id: 2, name: 'B', cash: '999.0000', note: 'B note' },
  ];
  page.selectedBookId.value = 1;
  return page;
}

test('switching books cancels the old edit and cannot write it into the new book', async () => {
  const writes = [];
  const page = bookPage({ updatePositionBook: (...args) => writes.push(args) });
  page.startEditBook();
  assert.equal(page.editForm.cash, '123.4500');
  page.selectedBookId.value = 2;
  assert.equal(page.editingBook.value, false);
  await page.saveBookEdit();
  assert.deepEqual(writes, []);
});

test('lost trade response retries the same request with the same key; changed payload gets a new key', async () => {
  const calls = [];
  const page = bookPage({
    createPositionTrade: async (...args) => { calls.push(args); throw new Error('response lost'); },
  });
  Object.assign(page.tradeForm, { symbol: '600000.SH', price: '10.00' });
  await page.handleRecordTrade();
  await page.handleRecordTrade();
  assert.equal(calls.length, 2);
  assert.equal(calls[0][2], calls[1][2]);
  page.tradeForm.price = '11.00';
  await page.handleRecordTrade();
  assert.notEqual(calls[1][2], calls[2][2]);
});

test('old book response cannot overwrite the current book, including after current load failure', async () => {
  const first = deferred();
  const second = deferred();
  const page = bookPage({
    fetchPositionBookHoldings: id => id === 1 ? first.promise : second.promise,
    fetchPositionTrades: async id => [{ id }],
  });
  const oldLoad = page.refreshBookData();
  page.selectedBookId.value = 2;
  const newLoad = page.refreshBookData();
  second.reject(new Error('B unavailable'));
  await newLoad;
  first.resolve({ book_id: 1 });
  await oldLoad;
  assert.equal(page.holdings.value, null);
  assert.deepEqual(page.trades.value, []);
  assert.equal(page.actionError.value, 'B unavailable');
});

test('retry after a committed trade loses its response creates only one server fact', async () => {
  const facts = new Map();
  let requests = 0;
  const page = bookPage({
    createPositionTrade: async (bookId, payload, key) => {
      if (!facts.has(key)) facts.set(key, { bookId, ...payload });
      if (++requests === 1) throw new Error('connection closed after commit');
      return facts.get(key);
    },
    fetchPositionBookHoldings: async () => ({ book_id: 1 }),
    fetchPositionTrades: async () => [...facts.values()],
  });
  Object.assign(page.tradeForm, { symbol: '600000.SH', price: '10.00' });
  await page.handleRecordTrade();
  assert.equal(facts.size, 1);
  await page.handleRecordTrade();
  assert.equal(requests, 2);
  assert.equal(facts.size, 1);
  assert.equal(page.trades.value.length, 1);
  assert.equal(page.actionError.value, null);
});

test('switching books clears holdings, drift and notices immediately', () => {
  const page = bookPage();
  page.holdings.value = { book_id: 1 };
  page.drift.value = { plan_id: 1 };
  page.notice.value = 'A updated';
  page.selectedBookId.value = 2;
  assert.equal(page.holdings.value, null);
  assert.equal(page.drift.value, null);
  assert.equal(page.notice.value, null);
});

test('missing trusted prices clear old drift and show the affected symbols', async () => {
  class StructuredDriftError extends Error {
    constructor(message, code, symbols) {
      super(message);
      this.code = code;
      this.symbols = symbols;
    }
  }
  const page = bookPage({
    PortfolioDriftApiError: StructuredDriftError,
    fetchPortfolioPlanDrift: async () => {
      throw new StructuredDriftError(
        'legacy-compatible detail',
        'portfolio_drift_missing_trusted_prices',
        ['000001.SZ', '600000.SH'],
      );
    },
  });
  page.driftPlanId.value = '3';
  page.drift.value = { plan_id: 2 };

  await page.handleFetchDrift();

  assert.equal(page.drift.value, null);
  assert.equal(
    page.actionError.value,
    '以下非零持仓缺少可信价格，暂不能计算组合偏离：000001.SZ、600000.SH。请先补齐或修复可信行情后重试。',
  );
});

test('position quote coverage distinguishes missing prices from older trusted quotes', () => {
  const page = bookPage();
  page.holdings.value = {
    price_as_of: '2026-11-16',
    holdings: [
      { symbol: 'A', price: '10', price_as_of: '2026-11-16', price_stale: false },
      { symbol: 'B', price: '12', price_as_of: '2026-11-13', price_stale: false },
      { symbol: 'C', price: null, price_as_of: null, price_stale: true },
    ],
  };
  assert.deepEqual(page.quoteCoverage.value, { total: 3, priced: 2, missing: 1, earlier: 1 });
  page.holdings.value = null;
  assert.deepEqual(page.quoteCoverage.value, { total: 0, priced: 0, missing: 0, earlier: 0 });
});

test('drift highlights holdings outside the plan and older quote dates', () => {
  const page = bookPage();
  page.drift.value = {
    price_as_of: '2026-11-16',
    rows: [
      { symbol: 'A', in_plan: true, price_as_of: '2026-11-16' },
      { symbol: 'B', in_plan: false, price_as_of: '2026-11-13' },
      { symbol: 'C', in_plan: false, price_as_of: '2026-11-16' },
    ],
  };
  assert.equal(page.driftOutsidePlanCount.value, 2);
  assert.equal(page.driftEarlierQuoteCount.value, 1);
  page.drift.value = null;
  assert.equal(page.driftOutsidePlanCount.value, 0);
  assert.equal(page.driftEarlierQuoteCount.value, 0);
});

test('changed drift inputs and book refresh discard old advice and late responses', async () => {
  const pending = deferred();
  const page = bookPage({
    fetchPortfolioPlanDrift: async () => pending.promise,
    fetchPositionBookHoldings: async () => ({ holdings: [] }),
    fetchPositionTrades: async () => [],
  });
  page.driftPlanId.value = '3';
  const request = page.handleFetchDrift();
  assert.equal(page.driftLoading.value, true);
  page.driftThreshold.value = '0.05';
  assert.equal(page.drift.value, null);
  assert.equal(page.driftLoading.value, false);
  pending.resolve({ plan_id: 3, rows: [] });
  await request;
  assert.equal(page.drift.value, null);

  page.drift.value = { plan_id: 3, rows: [] };
  const refresh = page.refreshBookData();
  assert.equal(page.drift.value, null);
  await refresh;
  page.drift.value = { plan_id: 3, rows: [] };
  page.driftPlanId.value = '4';
  assert.equal(page.drift.value, null);
});

test('position drift discovers paged plans and keeps the selected draft', async () => {
  const requests = [];
  const page = bookPage({
    fetchPortfolioPlans: async (limit, offset) => {
      requests.push({ limit, offset });
      return offset === 0
        ? { items: [
          { plan_id: 3, name: 'newer', as_of_date: '2026-11-16' },
          { plan_id: 2, name: 'older', as_of_date: '2026-11-15' },
        ], total: 3 }
        : { items: [{ plan_id: 1, name: 'oldest', as_of_date: '2026-11-14' }], total: 3 };
    },
  });
  await page.loadPlanOptions(true);
  assert.deepEqual(page.planOptions.value.map(plan => plan.plan_id), [3, 2]);
  assert.equal(page.planOptionsLoaded.value, true);
  page.driftPlanId.value = '2';
  assert.equal(page.selectedPlan.value.name, 'older');
  await page.loadPlanOptions();
  assert.deepEqual(requests, [{ limit: 50, offset: 0 }, { limit: 50, offset: 2 }]);
  assert.deepEqual(page.planOptions.value.map(plan => plan.plan_id), [3, 2, 1]);
  assert.equal(page.driftPlanId.value, '2');
  page.driftPlanId.value = '99';
  assert.equal(page.selectedPlan.value, null);
});

test('failed plan discovery still permits drift lookup by manual ID', async () => {
  const requests = [];
  const result = { plan_id: 99, rows: [] };
  const page = bookPage({
    fetchPortfolioPlans: async () => { throw new Error('list unavailable'); },
    fetchPortfolioPlanDrift: async (...args) => { requests.push(args); return result; },
  });
  await page.loadPlanOptions(true);
  assert.match(page.planOptionsError.value, /list unavailable/);
  assert.equal(page.planOptionsLoading.value, false);
  page.driftPlanId.value = '99';
  await page.handleFetchDrift();
  assert.deepEqual(requests, [[99, { positionBookId: 1, threshold: '0.02' }]]);
  assert.deepEqual(page.drift.value, result);
  assert.equal(page.actionError.value, null);
});

test('plan picker and selected summary distinguish the same date across years', () => {
  const filename = new URL('../src/pages/PositionsPage.vue', import.meta.url);
  const { descriptor } = parse(readFileSync(filename, 'utf8'));
  const source = descriptor.template.content;
  const fragments = [
    source.match(/<option v-for="item in planOptions"[\s\S]*?<\/option>/)[0],
    source.match(/<p v-if="selectedPlan"[\s\S]*?<\/p>/)[0],
    source.match(/<p class="section-note">草案「[\s\S]*?<\/p>/)[0],
  ].join('');
  const compiled = compileTemplate({ source: fragments, filename: String(filename), id: 'plan-dates' });
  assert.deepEqual(compiled.errors, []);
  const { outputText } = ts.transpileModule(compiled.code, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const module = { exports: {} };
  new Function('require', 'module', 'exports', outputText)(() => vue, module, module.exports);
  const plans = [2025, 2026].map((year, index) => ({
    plan_id: index + 1, name: 'same day', as_of_date: `${year}-11-17`, total_equity: '1000',
  }));
  const collectText = node => typeof node === 'string' ? node
    : Array.isArray(node) ? node.map(collectText).join('') : collectText(node?.children ?? '');
  for (const selectedPlan of plans) {
    const tree = module.exports.render({
      driftPlanId: String(selectedPlan.plan_id), planOptions: plans, planOptionsLoading: false,
      selectedPlan, formatNumber: value => value,
      drift: { plan_name: 'same day', plan_as_of_date: selectedPlan.as_of_date,
        plan_total_equity: '1000', total_equity: '1000' },
    }, []);
    const rendered = collectText(tree);
    assert.match(rendered, /2025-11-17/);
    assert.match(rendered, /2026-11-17/);
    assert.ok(rendered.includes(`草案截至 ${selectedPlan.as_of_date}`));
    assert.ok(rendered.includes(`草案「same day」截至 ${selectedPlan.as_of_date}`));
  }
});

test('portfolio drift API error parsing supports new, legacy and non-JSON responses', async () => {
  const originalFetch = globalThis.fetch;
  const responses = [
    new Response(JSON.stringify({
      detail: 'missing prices',
      code: 'portfolio_drift_missing_trusted_prices',
      symbols: ['600000.SH'],
    }), { status: 422, headers: { 'content-type': 'application/json' } }),
    new Response(JSON.stringify({ detail: 'legacy detail' }), {
      status: 422,
      headers: { 'content-type': 'application/json' },
    }),
    new Response('upstream unavailable', { status: 503 }),
  ];
  globalThis.fetch = async () => responses.shift();
  try {
    await assert.rejects(
      () => fetchPortfolioPlanDrift(3, { positionBookId: 1 }),
      error => error instanceof PortfolioDriftApiError
        && error.message === 'missing prices'
        && error.status === 422
        && error.code === 'portfolio_drift_missing_trusted_prices'
        && error.symbols[0] === '600000.SH',
    );
    await assert.rejects(
      () => fetchPortfolioPlanDrift(3, { positionBookId: 1 }),
      error => error instanceof PortfolioDriftApiError
        && error.message === 'legacy detail'
        && error.code === null
        && error.symbols.length === 0,
    );
    await assert.rejects(
      () => fetchPortfolioPlanDrift(3, { positionBookId: 1 }),
      error => error instanceof PortfolioDriftApiError
        && error.message === 'Plan drift failed with 503'
        && error.status === 503,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('paper account analysis ignores delayed responses for previous account', async () => {
  const first = deferred();
  const second = deferred();
  const page = setup('pages/PaperTradingPage.vue', {
    fetchPaperAccountPositions: id => id === 1 ? first.promise : second.promise,
    fetchPaperAccountPerformance: async id => ({ account_id: id }),
  });
  page.accountId.value = '1';
  page.accountId.value = '2';
  second.resolve({ account_id: 2 });
  await new Promise(resolve => setImmediate(resolve));
  first.resolve({ account_id: 1 });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(page.paperPositions.value.account_id, 2);
  assert.equal(page.paperPerformance.value.account_id, 2);
});

test('copying backtest weights uses total equity as zero-position budget and preserves decimals', async () => {
  const page = portfolioPlan({
    fetchBacktestFinalHoldings: async () => ({
      as_of_date: '2026-10-26', cash: '100000.0000', total_equity: '1000000.0000',
      holdings: [{ symbol: '600000.SH', security_type: 'stock', close: '10.0000', weight: '0.9000000000' }],
    }),
  }, { writeEnabled: true });
  page.prefillRunId.value = '7';
  await page.prefillFromBacktestRun();
  assert.equal(page.form.cash, '1000000.0000');
  assert.equal(page.form.as_of_date, '2026-10-26');
  assert.equal(page.form.assets[0].current_quantity, 0);
  assert.equal(page.form.assets[0].requested_weight, '0.9000000000');
  assert.match(page.prefillNote.value, /2026-10-26/);
  assert.match(page.prefillNote.value, /当前持仓设为零/);
});

test('portfolio risk request keeps opt-in budgets explicit and exact', () => {
  const page = portfolioPlan({}, { writeEnabled: true });
  assert.equal(page.buildPlanRequest().constraints.risk_budget, undefined);
  page.riskBudgetEnabled.value = true;
  page.styleBudgetEnabled.value = true;
  page.trackingErrorBudgetEnabled.value = true;
  page.volatilityBudgetEnabled.value = true;
  page.riskBudgetDraft.max_industry_weight = '0.3500000000';
  page.riskBudgetDraft.max_style_exposure = '0.1234567890';
  page.riskBudgetDraft.max_tracking_error = '0.0800000000';
  page.riskBudgetDraft.max_portfolio_volatility = '0.2000000000';
  assert.throws(() => page.buildPlanRequest(), /基准代码/);
  page.riskBudgetDraft.benchmark = '000300.sh';
  const request = page.buildPlanRequest();
  assert.deepEqual(request.constraints.risk_budget, {
    max_industry_weight: '0.3500000000',
    max_style_exposure: '0.1234567890',
    max_tracking_error: '0.0800000000',
    max_portfolio_volatility: '0.2000000000',
    benchmark: '000300.SH',
  });
  page.trackingErrorBudgetEnabled.value = false;
  assert.equal(page.buildPlanRequest().constraints.risk_budget.benchmark, null);
  assert.equal(page.buildPlanRequest().constraints.risk_budget.max_tracking_error, null);
});

test('plan retries keep their key only while the submitted payload is unchanged', async () => {
  const calls = [];
  const page = portfolioPlan({
    createPortfolioPlan: async (request, key) => {
      calls.push({ request, key });
      throw new Error('response lost');
    },
  }, { writeEnabled: true });
  await page.submitPlan();
  await page.submitPlan();
  assert.equal(calls[0].key, calls[1].key);
  page.form.cash = '100001';
  await page.submitPlan();
  assert.notEqual(calls[1].key, calls[2].key);
});

test('book prefill rejects missing quotes and preserves exact holdings inputs', async () => {
  let missing = true;
  const page = portfolioPlan({
    fetchPositionBooks: async () => [{ id: 1, name: 'ledger' }],
    fetchPositionBookHoldings: async () => ({
      cash: '12345.6789',
      holdings: [
        { symbol: '600000.SH', quantity: 100, price: '10.000000', price_as_of: '2026-11-13', weight: '0.1234567890', security_type: 'stock', industry: '银行' },
        { symbol: '000001.SZ', quantity: 100, price: missing ? null : '20.000000', price_as_of: '2026-11-13', weight: '0.2345678901', security_type: 'stock', industry: '银行' },
      ],
    }),
  }, { writeEnabled: true });
  await page.prefillFromPositionBook();
  assert.match(page.errorMessage.value, /000001.SZ.*停止预填/);
  assert.equal(page.form.assets[0].symbol, '');
  missing = false;
  await page.prefillFromPositionBook();
  assert.equal(page.form.cash, '12345.6789');
  assert.equal(page.form.as_of_date, '2026-11-13');
  assert.match(page.prefillNote.value, /账本.*2026-11-13/);
  assert.equal(page.form.assets[0].requested_weight, '0.1234567890');
  assert.equal(page.form.assets.length, 2);
});

function researchRow(symbol, { eligible = true, status = 'ready_for_review', diagnostics = true } = {}) {
  return {
    shortlist: {
      status: eligible && status === 'ready_for_review' && diagnostics ? 'selected' : 'excluded',
      selected: eligible && status === 'ready_for_review' && diagnostics,
      qualified: eligible && status === 'ready_for_review' && diagnostics,
      rank: 1, reason_codes: [], metrics: {},
    },
    symbol, name: symbol, status, reason_codes: status === 'ready_for_review' ? [] : ['industry_missing'],
    pe_vendor_unverified: '9', pb_vendor: '0.8', roe_reported_vendor: '0.12', roe_report_date: '2026-06-30',
    research_diagnostics: diagnostics ? {
      version: 'research-v1', review_eligible: eligible,
      trend: { status: eligible ? 'improving' : 'weak', return_20: eligible ? '0.1' : '-0.2', ma20_gap: '0.01', reason_codes: [], source_refs: [] },
      quality: { status: 'supported', net_income_sum_3y: '100', ocf_profit_3y: '1.1', ocf_ttm: '30', reason_codes: [], source_refs: [] },
    } : undefined,
  };
}

function screenReport(overrides = {}) {
  return {
    screen_version: 'relative_value_research_v7',
    selection: { version: 'value_research_shortlist_v2', ordered_symbols: ['600001.SH'],
      qualified_count: 1, shortlist_count: 1, parameters: {}, reason_counts: {} },
    status: 'partial', global_reason_codes: [], ready_count: 3,
    target_trade_date: '2026-10-28', roe_report_dates: ['2026-06-30'],
    freshness: { status: 'current', expected_trade_date: '2026-10-28', checked_at: '2026-10-29T00:01:00+08:00', reason_codes: [] },
    candidate_eligible: true, diagnostic_version: 'research-v1',
    rows: [researchRow('600001.SH'), researchRow('600002.SH', { eligible: false }),
      researchRow('600003.SH', { status: 'needs_review' }), researchRow('600004.SH', { diagnostics: false })],
    ...overrides,
  };
}

test('daily screen defaults to server shortlist and never promotes raw records', async () => {
  const page = setup('pages/components/PriceValueScreenPanel.vue', { fetchPriceValueScreen: async () => screenReport() });
  await page.refresh();
  assert.equal(page.showAll.value, false);
  assert.deepEqual(page.visible.value.map(row => row.symbol), ['600001.SH']);
  page.viewMode.value = 'base';
  assert.deepEqual(page.visible.value.map(row => row.symbol), ['600001.SH', '600002.SH', '600004.SH']);
  page.showAll.value = true;
  assert.equal(page.visible.value.length, 4);
  assert.equal(page.researchRows.value.length, 1);
  page.showAll.value = false;
  assert.equal(page.visible.value.length, 3);
});

test('stale, future, unknown and legacy daily reports cannot produce current candidates', async () => {
  for (const status of ['stale', 'future', 'unknown', undefined]) {
    const report = screenReport({ freshness: status ? { status, reason_codes: [] } : undefined });
    const page = setup('pages/components/PriceValueScreenPanel.vue', { fetchPriceValueScreen: async () => report });
    await page.refresh();
    assert.equal(page.visible.value.length, 0, status);
    page.viewMode.value = 'base';
    page.maxPe.value = 1;
    page.clearFilters();
    assert.equal(page.visible.value.length, 0, status);
    assert.equal(page.currentReportEligible.value, false, status);
    page.showAll.value = true;
    assert.equal(page.visible.value.length, 4, 'raw history requires explicit opt-in');
    assert.equal(page.researchRows.value.length, 0);
  }
});

test('zero-ready blocked daily report stays empty until raw records are explicitly opened', async () => {
  const report = screenReport({ status: 'blocked', ready_count: 0, global_reason_codes: ['no_reviewable_candidates'], candidate_eligible: false });
  const page = setup('pages/components/PriceValueScreenPanel.vue', { fetchPriceValueScreen: async () => report });
  await page.refresh();
  assert.equal(page.showAll.value, false);
  assert.equal(page.visible.value.length, 0);
  page.showAll.value = true;
  assert.equal(page.visible.value.length, 4);
  await page.refresh();
  assert.equal(page.showAll.value, false);
  assert.equal(page.visible.value.length, 0);
});

test('date-current legacy report remains blocked and explains the missing research version', async () => {
  const report = screenReport({ candidate_eligible: false, diagnostic_version: undefined });
  const page = setup('pages/components/PriceValueScreenPanel.vue', { fetchPriceValueScreen: async () => report });
  await page.refresh();
  assert.equal(page.currentReportEligible.value, false);
  assert.equal(page.visible.value.length, 0);
  assert.match(page.unavailableReason.value, /旧报告没有新版走势与质量诊断/);
  page.viewMode.value = 'base';
  page.clearFilters();
  assert.equal(page.visible.value.length, 0);
  assert.equal(page.researchRows.value.length, 0);
});

test('changing daily screen view resets pagination and clearing valuation preserves research gates', async () => {
  const page = setup('pages/components/PriceValueScreenPanel.vue', { fetchPriceValueScreen: async () => screenReport() });
  await page.refresh();
  page.page.value = 3;
  page.viewMode.value = 'base';
  assert.equal(page.page.value, 1);
  page.page.value = 3;
  page.maxPe.value = 5;
  assert.equal(page.page.value, 1);
  assert.equal(page.visible.value.length, 0);
  page.viewMode.value = 'shortlist';
  page.clearFilters();
  assert.equal(page.viewMode.value, 'shortlist');
  assert.equal(page.showAll.value, false);
  assert.deepEqual(page.visible.value.map(row => row.symbol), ['600001.SH']);
});

test('daily screen keeps qualified pool separate and filters never refill server shortlist', async () => {
  const selected = researchRow('600002.SH');
  const deferred = researchRow('600001.SH');
  deferred.shortlist = { ...deferred.shortlist, status: 'qualified_deferred', selected: false, rank: 2 };
  const other = researchRow('600003.SH');
  other.shortlist = { ...other.shortlist, status: 'excluded', qualified: false, selected: false };
  const report = screenReport({
    rows: [deferred, other, selected],
    selection: { version: 'value_research_shortlist_v2', ordered_symbols: ['600002.SH'],
      qualified_count: 2, shortlist_count: 1, parameters: {}, reason_counts: {} },
  });
  const page = setup('pages/components/PriceValueScreenPanel.vue', { fetchPriceValueScreen: async () => report });
  await page.refresh();
  assert.deepEqual(page.visible.value.map(row => row.symbol), ['600002.SH']);
  assert.equal(page.qualifiedRows.value.length, 2);
  assert.equal(page.researchRows.value.length, 3);
  page.maxPe.value = 5;
  assert.equal(page.visible.value.length, 0, 'deferred stock must not fill an excluded visible slot');
  page.clearFilters();
  assert.deepEqual(page.visible.value.map(row => row.symbol), ['600002.SH']);
  page.viewMode.value = 'qualified';
  assert.deepEqual(page.visible.value.map(row => row.symbol), ['600001.SH', '600002.SH']);
  page.viewMode.value = 'research';
  assert.equal(page.visible.value.length, 3);
  await page.refresh();
  assert.equal(page.viewMode.value, 'shortlist');
});

test('daily screen uses backend order instead of code order and never auto-fills empty selection', async () => {
  const first = researchRow('600001.SH');
  const second = researchRow('600002.SH');
  let report = screenReport({ rows: [first, second], selection: {
    version: 'value_research_shortlist_v2', ordered_symbols: ['600002.SH', '600001.SH'],
    qualified_count: 2, shortlist_count: 2, parameters: {}, reason_counts: {},
  } });
  const page = setup('pages/components/PriceValueScreenPanel.vue', { fetchPriceValueScreen: async () => report });
  await page.refresh();
  assert.deepEqual(page.visible.value.map(row => row.symbol), ['600002.SH', '600001.SH']);
  report = { ...report, selection: { ...report.selection, ordered_symbols: [] } };
  await page.refresh();
  assert.equal(page.visible.value.length, 0);
  assert.equal(page.researchRows.value.length, 2);
});

test('daily screen refuses legacy selection version even if original research diagnostics pass', async () => {
  for (const selection of [undefined, { version: 'legacy', ordered_symbols: ['600001.SH'] }, { version: 'value_research_shortlist_v1', ordered_symbols: ['600001.SH'] }]) {
    const page = setup('pages/components/PriceValueScreenPanel.vue', {
      fetchPriceValueScreen: async () => screenReport({ selection }),
    });
    await page.refresh();
    assert.equal(page.currentReportEligible.value, false);
    assert.equal(page.visible.value.length, 0);
    assert.match(page.unavailableReason.value, /旧报告没有新版精选规则/);
    page.showAll.value = true;
    assert.equal(page.visible.value.length, 4);
  }
});

test('daily screen keeps frozen forward tracking separate from personal filters', async () => {
  const forward = {
    version: 'price_value_forward_observation_v1', status: 'observing', reason_codes: [],
    snapshot_hash: 'abc123', formation_cutoff: '2026-10-29', entry_date: '2026-10-30',
    observation_mode: 'current_research_catchup', benchmark: 'research_pool_equal_weight',
    cost_policy: { version: 'fixed', round_trip_cost_rate: '0.001', description: 'fixture' },
    pools: { shortlist: ['600001.SH'], qualified: ['600001.SH'], research: ['600001.SH'] },
    windows: [
      { trading_days: 5, status: 'complete', end_date: '2026-11-06', reason_codes: [], pools: {
        shortlist: { status: 'complete', member_count: 1, observed_count: 1, missing_count: 0,
          coverage_rate: '1', gross_return_observed: '0.1', net_return_observed: '0.099',
          excess_return_vs_research_pool: '0', positive_member_rate: '1', maximum_member_loss: '0.1', missing_reason_counts: {} },
        qualified: { status: 'complete', member_count: 1, observed_count: 1, missing_count: 0,
          coverage_rate: '1', gross_return_observed: '0.1', net_return_observed: '0.099',
          excess_return_vs_research_pool: '0', positive_member_rate: '1', maximum_member_loss: '0.1', missing_reason_counts: {} },
        research: { status: 'complete', member_count: 1, observed_count: 1, missing_count: 0,
          coverage_rate: '1', gross_return_observed: '0.1', net_return_observed: '0.099',
          excess_return_vs_research_pool: '0', positive_member_rate: '1', maximum_member_loss: '0.1', missing_reason_counts: {} },
      } },
      { trading_days: 20, status: 'pending', end_date: null, reason_codes: ['window_not_complete'], pools: {} },
    ], research_only: true, pit_verified: false, execution_eligible: false,
  };
  const page = setup('pages/components/PriceValueScreenPanel.vue', {
    fetchPriceValueScreen: async () => screenReport(),
    fetchPriceValueTracking: async () => forward,
  });
  await page.refresh();
  assert.equal(page.tracking.value.snapshot_hash, 'abc123');
  page.maxPe.value = 1;
  assert.equal(page.visible.value.length, 0);
  assert.equal(page.tracking.value.windows[0].pools.shortlist.member_count, 1);
});

test('forward tracking failure does not hide the current screen report', async () => {
  const page = setup('pages/components/PriceValueScreenPanel.vue', {
    fetchPriceValueScreen: async () => screenReport(),
    fetchPriceValueTracking: async () => { throw new Error('tracking unavailable'); },
  });

  await page.refresh();

  assert.equal(page.error.value, '');
  assert.equal(page.tracking.value, null);
  assert.equal(page.trackingError.value, 'tracking unavailable');
  assert.deepEqual(page.visible.value.map(row => row.symbol), ['600001.SH']);
});

test('backtest list separates saved, derived, missing equity and zero volatility', async () => {
  const oldFetch = globalThis.fetch;
  const run = { id: 1, status: 'success', sharpe_ratio: '1.25',
    performance_status: 'analyzed', performance_code_version: 'analysis-v2' };
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ items: [run], total: 1 }) });
  try {
    const page = setup('components/operations/RunTablesSection.vue', {
      formatNumber: value => String(value),
    }, { factors: { latest_runs: [] }, backtests: { latest_runs: [] } });
    await page.loadRuns();
    assert.equal(page.runPage.value[0].performance_code_version, 'analysis-v2');
    assert.equal(page.sharpeText(run), '1.25');
    assert.equal(page.sharpeText({ ...run, sharpe_ratio: null }), '不可计算');
    assert.equal(page.sharpeText({ ...run, performance_status: 'derived' }), '1.25');
    assert.equal(page.sharpeText({ ...run, performance_status: 'missing_equity', sharpe_ratio: null }), '缺净值');
    assert.equal(page.sharpeText({ ...run, performance_status: 'invalid_equity', sharpe_ratio: null }), '净值无效');
    assert.equal(page.sharpeText({ ...run, status: 'failed' }), '—');
  } finally { globalThis.fetch = oldFetch; }
});

test('backtest comparison keeps cross-page selection and reports preview failures', async () => {
  const oldFetch = globalThis.fetch;
  const first = { id: 1, name: 'first', status: 'success', performance_status: 'derived', sharpe_ratio: '1' };
  const second = { id: 2, name: 'second', status: 'success', performance_status: 'missing_equity', sharpe_ratio: null };
  let fail = false;
  let pending = false;
  let resolvePending;
  globalThis.fetch = async (path, init = {}) => {
    if (String(path).includes('offset=20')) return { ok: true, json: async () => ({ items: [second], total: 21 }) };
    if (String(path).includes('compare-preview')) {
      assert.deepEqual(JSON.parse(init.body).run_ids, [1, 2]);
      if (pending) return new Promise(resolve => { resolvePending = resolve; });
      return fail
        ? { ok: false, status: 422, json: async () => ({ detail: 'comparison unavailable' }) }
        : { ok: true, json: async () => [{ run_id: 1, metric_source: 'derived' }, { run_id: 2, metric_source: 'missing_equity' }] };
    }
    return { ok: true, json: async () => ({ items: [first], total: 21 }) };
  };
  try {
    const page = setup('components/operations/RunTablesSection.vue', {}, { factors: { latest_runs: [] }, backtests: { latest_runs: [] } });
    await page.loadRuns(0);
    page.toggleSelection(first);
    await page.loadRuns(20);
    page.toggleSelection(second);
    assert.deepEqual(page.selectedRuns.value.map(run => run.id), [1, 2]);
    await page.compareSelected();
    assert.equal(page.comparison.value.length, 2);
    fail = true;
    await page.compareSelected();
    assert.match(page.comparisonError.value, /comparison unavailable/);
    fail = false;
    pending = true;
    const stale = page.compareSelected();
    page.toggleSelection(second);
    resolvePending({ ok: true, json: async () => [{ run_id: 1 }] });
    await stale;
    assert.equal(page.comparison.value.length, 0);
    page.clearSelection();
    assert.equal(page.selectedRuns.value.length, 0);
  } finally { globalThis.fetch = oldFetch; }
});

test('backtest comparison link restores order and keeps a changed baseline in the URL', async () => {
  const oldFetch = globalThis.fetch;
  const route = { query: { runs: '10,13' } };
  const writes = [];
  const requests = [];
  const preview = ids => ids.map(id => ({ run_id: id, name: `run-${id}`, delta_status: id === ids[0] ? 'baseline' : 'available', strategy_differences: [] }));
  globalThis.fetch = async (path, init = {}) => {
    assert.match(String(path), /compare-preview/);
    const ids = JSON.parse(init.body).run_ids;
    requests.push(ids);
    return { ok: true, json: async () => preview(ids) };
  };
  try {
    const page = setup('components/operations/RunTablesSection.vue', {
      useRoute: () => route,
      useRouter: () => ({ replace: async ({ query }) => { writes.push(query.runs); route.query = query; } }),
    }, {});
    await page.restoreSelectionFromRoute();
    assert.deepEqual(page.selectedRuns.value.map(run => run.id), [10, 13]);
    assert.deepEqual(requests, [[10, 13]]);
    assert.equal(page.comparison.value.length, 2);

    page.setBaseline(13);
    assert.equal(page.linkedDetailQuery().runs, '13,10');
    await Promise.resolve();
    assert.equal(route.query.runs, '13,10');
    assert.equal(page.comparison.value.length, 0);
    await page.compareSelected();
    assert.deepEqual(requests.at(-1), [13, 10]);
    page.clearSelection();
    await Promise.resolve();
    assert.equal(route.query.runs, undefined);
    assert.deepEqual(writes, ['13,10', undefined]);
  } finally { globalThis.fetch = oldFetch; }
});

test('bad or stale backtest links keep their error and cannot overwrite a newer selection', async () => {
  const oldFetch = globalThis.fetch;
  const route = { query: { runs: '10,10' } };
  let resolvePreview;
  globalThis.fetch = async (path) => {
    if (String(path).includes('/runs/12')) return { ok: true, json: async () => ({ id: 12, name: 'failed', status: 'failed' }) };
    return new Promise(resolve => { resolvePreview = resolve; });
  };
  try {
    const page = setup('components/operations/RunTablesSection.vue', {
      useRoute: () => route,
      useRouter: () => ({ replace: async ({ query }) => { route.query = query; } }),
    }, {});
    await page.restoreSelectionFromRoute();
    assert.match(page.comparisonError.value, /重复/);
    assert.equal(page.selectedRuns.value.length, 0);
    page.clearSelection();
    await Promise.resolve();
    assert.equal(route.query.runs, undefined);

    route.query = { runs: '12' };
    await page.restoreSelectionFromRoute();
    assert.match(page.comparisonError.value, /不可用于比较/);
    assert.equal(route.query.runs, '12');
    assert.equal(page.selectedRuns.value.length, 0);

    route.query = { runs: '10,13' };
    const stale = page.restoreSelectionFromRoute();
    page.toggleSelection({ id: 99, name: 'new choice', status: 'success' });
    resolvePreview({ ok: true, json: async () => [
      { run_id: 10, name: 'old-10' }, { run_id: 13, name: 'old-13' },
    ] });
    await stale;
    assert.deepEqual(page.selectedRuns.value.map(run => run.id), [99]);
    assert.equal(page.comparison.value.length, 0);
    assert.equal(route.query.runs, '99');
  } finally { globalThis.fetch = oldFetch; }
});

test('rapid backtest selection leaves the latest ordered IDs in the link', async () => {
  const route = { path: '/backtests', query: {} };
  let finishFirst;
  let writes = 0;
  const page = setup('components/operations/RunTablesSection.vue', {
    useRoute: () => route,
    useRouter: () => ({ replace: ({ query }) => {
      writes++;
      if (writes === 1) return new Promise(resolve => { finishFirst = () => { route.query = query; resolve(); }; });
      route.query = query;
      return Promise.resolve();
    } }),
  }, {});
  page.toggleSelection({ id: 10, name: 'first', status: 'success' });
  await Promise.resolve();
  page.toggleSelection({ id: 13, name: 'second', status: 'success' });
  assert.equal(page.linkedDetailQuery().runs, '10,13');
  finishFirst();
  await page.routeWriteQueue;
  assert.equal(route.query.runs, '10,13');
});

test('backtest comparison follows same-page link changes and ignores older restores', async () => {
  const oldFetch = globalThis.fetch;
  const route = vue.reactive({ path: '/backtests', query: { runs: '10,13' } });
  const oldResponse = deferred();
  const clearedResponse = deferred();
  const requests = [];
  globalThis.fetch = async (_path, init) => {
    const ids = JSON.parse(init.body).run_ids;
    requests.push(ids);
    if (ids[0] === 10) return oldResponse.promise;
    if (ids[0] === 30) return clearedResponse.promise;
    return { ok: true, json: async () => ids.map(id => ({ run_id: id, name: `run-${id}` })) };
  };
  try {
    const page = setup('components/operations/RunTablesSection.vue', {
      useRoute: () => route,
      useRouter: () => ({ replace: async ({ query }) => { route.query = query; } }),
    }, {});
    const stale = page.restoreSelectionFromRoute();
    route.query = { runs: '20,21' };
    assert.deepEqual(requests, [[10, 13], [20, 21]]);
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(page.selectedRuns.value.map(run => run.id), [20, 21]);
    oldResponse.resolve({ ok: true, json: async () => [{ run_id: 10 }, { run_id: 13 }] });
    await stale;
    assert.deepEqual(page.selectedRuns.value.map(run => run.id), [20, 21]);

    route.query = { runs: '20,22' };
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(page.selectedRuns.value.map(run => run.id), [20, 22]);
    route.query = { runs: '30,31' };
    route.query = {};
    assert.deepEqual(page.selectedRuns.value, []);
    assert.equal(page.comparison.value.length, 0);
    clearedResponse.resolve({ ok: true, json: async () => [{ run_id: 30 }, { run_id: 31 }] });
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(page.selectedRuns.value, []);
    assert.equal(page.comparison.value.length, 0);
  } finally { globalThis.fetch = oldFetch; }
});

test('single successful backtest link restores through the run detail', async () => {
  const oldFetch = globalThis.fetch;
  const route = { query: { runs: '42' } };
  const requests = [];
  globalThis.fetch = async path => {
    requests.push(String(path));
    return { ok: true, json: async () => ({ id: 42, name: 'single', status: 'success' }) };
  };
  try {
    const page = setup('components/operations/RunTablesSection.vue', { useRoute: () => route }, {});
    await page.restoreSelectionFromRoute();
    assert.deepEqual(requests, ['/api/v1/backtests/runs/42']);
    assert.deepEqual(page.selectedRuns.value.map(run => run.id), [42]);
    assert.equal(page.linkedDetailQuery().runs, '42');
  } finally { globalThis.fetch = oldFetch; }
});

test('comparison equity reads every page and rejects an oversized or incomplete curve', async () => {
  const oldFetch = globalThis.fetch;
  const requests = [];
  let mode = 'complete';
  globalThis.fetch = async path => {
    requests.push(String(path));
    if (mode === 'oversized') return { ok: true, json: async () => ({ total: 10001, items: [] }) };
    if (String(path).includes('offset=5000')) return { ok: true, json: async () => ({
      total: 5001, items: mode === 'incomplete' ? [] : [{ run_id: 10, trade_date: '2026-01-06', total_equity: '101' }],
    }) };
    return { ok: true, json: async () => ({ total: 5001, items: Array.from({ length: 5000 }, () => ({
      run_id: 10, trade_date: '2026-01-05', total_equity: '100',
    })) }) };
  };
  try {
    const page = setup('components/operations/RunTablesSection.vue', {}, {});
    const rows = await page.readEquityRows(10, new AbortController().signal);
    assert.equal(rows.length, 5001);
    assert.match(requests[1], /offset=5000/);
    mode = 'incomplete';
    await assert.rejects(page.readEquityRows(10, new AbortController().signal), /分页缺失/);
    mode = 'oversized';
    await assert.rejects(page.readEquityRows(10, new AbortController().signal), /展示上限/);
  } finally { globalThis.fetch = oldFetch; }
});

test('comparison link loads only the baseline and selected aligned curve', async () => {
  const oldFetch = globalThis.fetch;
  const route = { query: { runs: '10,11,12' } };
  const preview = [
    { run_id: 10, name: 'base', initial_cash: '100', delta_status: 'baseline', delta_reasons: [] },
    { run_id: 11, name: 'aligned', initial_cash: '100', delta_status: 'available', delta_reasons: [] },
    { run_id: 12, name: 'misdated', initial_cash: '100', delta_status: 'available', delta_reasons: [] },
  ];
  const equityRequests = [];
  globalThis.fetch = async (path) => {
    if (String(path).includes('compare-preview')) return { ok: true, json: async () => preview };
    const id = Number(String(path).match(/runs\/(\d+)\/equity/)?.[1]);
    equityRequests.push(id);
    const dates = id === 12 ? ['2026-01-05', '2026-01-07'] : ['2026-01-05', '2026-01-06'];
    return { ok: true, json: async () => ({ total: 2, items: dates.map((trade_date, index) => ({
      run_id: id, trade_date, total_equity: index ? '110' : '90',
    })) }) };
  };
  try {
    const page = setup('components/operations/RunTablesSection.vue', { useRoute: () => route }, {});
    await page.restoreSelectionFromRoute();
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(page.selectedRuns.value.map(run => run.id), [10, 11, 12]);
    assert.deepEqual(equityRequests, [10, 11]);
    assert.deepEqual(page.comparisonCurves.value.map(curve => curve.runId), [10, 11]);
    assert.ok(Math.abs(page.comparisonCurves.value[0].returns[0] + 0.1) < 1e-12);
    assert.match(page.curveGeometry.value.lines[0].points, /45\.00,/);

    page.curveCandidateId.value = 12;
    page.selectCurveCandidate();
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(page.comparisonCurves.value, []);
    assert.match(page.curveNotes.value.join(''), /日期与基准不一致/);
    assert.equal(page.comparison.value.length, 3);
  } finally { globalThis.fetch = oldFetch; }
});

test('switching a comparison candidate ignores the previous slow equity response', async () => {
  const oldFetch = globalThis.fetch;
  const slow = deferred();
  const items = [
    { run_id: 10, name: 'base', initial_cash: '100', delta_status: 'baseline', delta_reasons: [] },
    { run_id: 11, name: 'slow', initial_cash: '100', delta_status: 'available', delta_reasons: [] },
    { run_id: 12, name: 'current', initial_cash: '100', delta_status: 'available', delta_reasons: [] },
  ];
  globalThis.fetch = async path => {
    const id = Number(String(path).match(/runs\/(\d+)\/equity/)?.[1]);
    if (id === 11) return slow.promise;
    return { ok: true, json: async () => ({ total: 2, items: [
      { run_id: id, trade_date: '2026-01-05', total_equity: '100' },
      { run_id: id, trade_date: '2026-01-06', total_equity: '101' },
    ] }) };
  };
  try {
    const page = setup('components/operations/RunTablesSection.vue', {}, {});
    page.comparison.value = items;
    page.curveCandidateId.value = 11;
    const first = page.loadComparisonCurves(items, 0);
    page.curveCandidateId.value = 12;
    const current = page.loadComparisonCurves(items, 0);
    await current;
    slow.resolve({ ok: true, json: async () => ({ total: 2, items: [
      { run_id: 11, trade_date: '2026-01-05', total_equity: '100' },
      { run_id: 11, trade_date: '2026-01-06', total_equity: '102' },
    ] }) });
    await first;
    assert.deepEqual(page.comparisonCurves.value.map(curve => curve.runId), [10, 12]);
  } finally { globalThis.fetch = oldFetch; }
});

test('comparison chart axis distinguishes small changes and shows intermediate dates', () => {
  const page = setup('components/operations/RunTablesSection.vue', {}, {});
  const dates = ['2026-01-05', '2026-01-06', '2026-01-07', '2026-01-08', '2026-01-09'];
  page.comparisonCurves.value = [
    { runId: 10, dates, returns: [0, 0, 0, 0, 0], drawdowns: [0, 0, 0, 0, 0] },
    { runId: 11, dates, returns: [0, 0.000001, 0.000002, 0.000001, 0], drawdowns: [0, 0, 0, 0, 0] },
  ];
  assert.notEqual(page.curveAxisText(page.curveGeometry.value.minimum), page.curveAxisText(page.curveGeometry.value.maximum));
  assert.deepEqual(page.curveDateTicks.value, dates);
  page.curveMode.value = 'drawdown';
  assert.equal(page.curveGeometry.value.flat, true);
});

test('backtest list can recover without an operations overview', async () => {
  const oldFetch = globalThis.fetch;
  let attempts = 0;
  globalThis.fetch = async () => {
    attempts++;
    if (attempts === 1) throw new Error('overview-independent API failure');
    return { ok: true, json: async () => ({ items: [{ id: 1, status: 'success' }], total: 1 }) };
  };
  try {
    const page = setup('components/operations/RunTablesSection.vue', {}, {});
    await page.loadRuns();
    assert.match(page.runError.value, /暂无历史运行/);
    await page.loadRuns();
    assert.equal(page.runError.value, '');
    assert.equal(page.loaded.value, true);
    assert.equal(page.runPage.value[0].id, 1);
  } finally { globalThis.fetch = oldFetch; }
});

test('backtest filters apply before paging and compare only matching windows', async () => {
  const oldFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async path => {
    requests.push(String(path));
    return { ok: true, json: async () => ({ items: [], total: 0 }) };
  };
  try {
    const page = setup('components/operations/RunTablesSection.vue', {}, {});
    await page.loadRuns();
    page.draftFilters.value.status = 'success';
    page.draftFilters.value.validation_mode = 'rolling';
    page.draftFilters.value.data_version = ' warm-b ';
    await page.applyFilters();
    assert.match(requests.at(-1), /status=success/);
    assert.match(requests.at(-1), /validation_mode=rolling/);
    assert.match(requests.at(-1), /data_version=warm-b/);
    await page.loadRuns(20);
    assert.match(requests.at(-1), /offset=20/);
    assert.match(requests.at(-1), /validation_mode=rolling/);

    await page.clearFilters();
    assert.equal(new URL(requests.at(-1), 'http://localhost').searchParams.has('validation_mode'), false);
    const count = requests.length;
    page.draftFilters.value.start_date = '2026-02-01';
    page.draftFilters.value.end_date = '2026-01-01';
    await page.applyFilters();
    assert.equal(requests.length, count);
    assert.match(page.filterError.value, /起始日不能晚于结束日/);
    assert.equal(page.validationModeLabel('none'), '探索性（未验证）');
  } finally { globalThis.fetch = oldFetch; }
});

test('backtest comparison changes the baseline and explains gated deltas', () => {
  const page = setup('components/operations/RunTablesSection.vue', {}, {});
  const first = { id: 1, name: 'first', status: 'success' };
  const second = { id: 2, name: 'second', status: 'success' };
  page.toggleSelection(first);
  page.toggleSelection(second);
  page.comparison.value = [{ run_id: 1 }];
  page.setBaseline(2);
  assert.deepEqual(page.selectedRuns.value.map(run => run.id), [2, 1]);
  assert.equal(page.comparison.value.length, 0);
  assert.equal(page.deltaStatusText({ delta_status: 'baseline', delta_reasons: [] }), '基准');
  assert.match(page.deltaStatusText({ delta_status: 'available', delta_reasons: [] }), /策略与输入差异见下/);
  assert.equal(page.deltaStatusText({ delta_status: 'unavailable', delta_reasons: ['metric_code_version', 'equity_dates_differ'] }), '指标代码版本不同或缺失；净值日期不同');
  assert.equal(page.percentagePointDelta('0.025'), '+2.5 个百分点');
  assert.equal(page.percentagePointDelta('-0.02'), '-2 个百分点');
  assert.equal(page.sharpeDeltaText({ delta_status: 'available', sharpe_ratio_delta: null }), '不可计算');
  assert.equal(page.sharpeDeltaText({ delta_status: 'unavailable', sharpe_ratio_delta: null }), '—');
  assert.equal(page.strategyDifferenceText({ delta_status: 'available', strategy_differences: ['factor_specs'] }), '1 项字段不同');
  assert.equal(page.strategyFieldLabel('factor_specs'), '因子配置');
  assert.equal(page.strategyContractText([{ factor_name: 'momentum_20', higher_is_better: false }]), '[{"factor_name":"momentum_20","higher_is_better":false}]');
  assert.match(page.strategyContractText([{ factor_name: 'momentum_20', factor_version: 'v1', weight: '1', higher_is_better: false }], 'factor_specs'), /momentum_20 \(v1\).*越低越好/);
  assert.equal(page.strategyContractText(['AAA', 'BBB'], 'symbols'), '共 2 只');
  assert.equal(page.symbolChangeSummary(['AAA', 'BBB', 'CCC'], ['BBB', 'DDD']), '交集 1 只；新增 1 只（如 DDD）；移除 2 只（如 AAA、CCC）');
  assert.equal(page.fullSymbolList(['AAA', 'BBB']), 'AAA、BBB');
  assert.equal(page.strategyDifferenceText({ delta_status: 'available', strategy_differences: [] }), '已记录预览字段无差异');
  assert.equal(page.strategyContractText(undefined), '未记录');
});

test('older backtest list response cannot replace newly filtered results', async () => {
  const oldFetch = globalThis.fetch;
  let releaseOld;
  globalThis.fetch = async path => {
    if (!String(path).includes('status=success')) {
      return new Promise(resolve => { releaseOld = resolve; });
    }
    return { ok: true, json: async () => ({ items: [{ id: 2 }], total: 1 }) };
  };
  try {
    const page = setup('components/operations/RunTablesSection.vue', {}, {});
    const stale = page.loadRuns();
    page.draftFilters.value.status = 'success';
    await page.applyFilters();
    releaseOld({ ok: true, json: async () => ({ items: [{ id: 1 }], total: 99 }) });
    await stale;
    assert.equal(page.runPage.value[0].id, 2);
    assert.equal(page.runTotal.value, 1);
    assert.equal(page.runLoading.value, false);
  } finally { globalThis.fetch = oldFetch; }
});

test('backtest detail calculates missing performance and keeps API failures distinct', async () => {
  const oldFetch = globalThis.fetch;
  let performanceStatus = 404;
  let postCount = 0;
  const json = value => ({ ok: true, json: async () => value });
  const run = { id: 1, name: 'fixture', strategy_name: 'factor_selection', status: 'success',
    parameters: {}, code_version: 'old-run-version', initial_cash: '10000',
    start_date: '2026-01-05', end_date: '2026-01-09', final_equity: '10100', total_return: '0.01',
    sharpe_ratio: '1.25', performance_status: 'derived', performance_code_version: 'current-analysis' };
  const report = { sharpe_ratio: '1.25', maximum_drawdown: '-0.1', code_version: 'current-analysis' };
  globalThis.fetch = async (path, init = {}) => {
    if (path === '/api/v1/backtests/runs/1') return json(run);
    if (path.endsWith('/performance') && init.method === 'POST') {
      postCount++;
      assert.equal(init.body, '{}');
      return json(report);
    }
    if (path.endsWith('/performance')) return {
      ok: false, status: performanceStatus,
      json: async () => ({ detail: performanceStatus === 404 ? 'not found' : 'service unavailable' }),
    };
    if (path.includes('/equity')) return json({ items: [], total: 0 });
    if (path.includes('/trades')) return json({ items: [], total: 0 });
    if (path.includes('/benchmark')) return json({ benchmark: 'fixture' });
    throw new Error(`unexpected request: ${path}`);
  };
  try {
    const page = setup('pages/BacktestRunPage.vue', {
      useRoute: () => ({ params: { runId: '1' } }),
      fetchBacktestFinalHoldingsTyped: async () => ({ holdings: [] }),
    });
    await page.load();
    assert.equal(page.performanceMissing.value, true);
    await page.calculatePerformance();
    assert.equal(postCount, 1);
    assert.equal(page.performance.value.sharpe_ratio, '1.25');
    assert.equal(page.performanceMissing.value, false);

    performanceStatus = 503;
    await page.load();
    assert.equal(page.performanceMissing.value, false);
    assert.match(page.performanceNote.value, /service unavailable/);
    await page.calculatePerformance();
    assert.equal(postCount, 1);
  } finally { globalThis.fetch = oldFetch; }
});
