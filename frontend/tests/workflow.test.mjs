import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import { compileScript, parse } from '@vue/compiler-sfc';
import ts from 'typescript';
import * as vue from 'vue';

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
    if (name.endsWith('/api')) return dependencies;
    if (name.startsWith('.') || name === '@lucide/vue') return dependencies;
    return require(name);
  };
  new Function('require', 'module', 'exports', outputText)(resolve, module, module.exports);
  return module.exports.default.setup(props, { expose() {}, emit() {} });
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function bookPage(api = {}) {
  const page = setup('pages/PositionsPage.vue', api);
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
  const page = setup('components/operations/PortfolioPlanSection.vue', {
    fetchBacktestFinalHoldings: async () => ({
      as_of_date: '2026-10-26', cash: '100000.0000', total_equity: '1000000.0000',
      holdings: [{ symbol: '600000.SH', security_type: 'stock', close: '10.0000', weight: '0.9000000000' }],
    }),
  }, { writeEnabled: true });
  page.prefillRunId.value = '7';
  await page.prefillFromBacktestRun();
  assert.equal(page.form.cash, '1000000.0000');
  assert.equal(page.form.assets[0].current_quantity, 0);
  assert.equal(page.form.assets[0].requested_weight, '0.9000000000');
  assert.match(page.backtestPrefillNote.value, /2026-10-26/);
  assert.match(page.backtestPrefillNote.value, /未复制历史实际持仓/);
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
