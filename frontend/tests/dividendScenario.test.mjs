import assert from 'node:assert/strict';
import test from 'node:test';
import { createDividendScenario, dividendSample } from '../src/composables/dividendScenario.ts';

test('dividend calculation is manual and snapshots string monetary inputs', async () => {
  let calls = 0;
  const state = createDividendScenario({ evaluate: async (payload) => {
    calls++;
    assert.equal(payload.initial_cash, '1000');
    assert.equal(payload.events[0].cash_per_share, '0.20');
    return { final_equity: '2000', execution_eligible: false };
  } });
  assert.equal(calls, 0);
  await state.calculate();
  assert.equal(state.report.value.final_equity, '2000');
  state.input.initial_cash = '2000';
  assert.equal(state.report.value, null);
  state.dispose();
});

test('changed input aborts pending request and ignores stale result', async () => {
  let done, signal;
  const state = createDividendScenario({ evaluate: async (_, value) => {
    signal = value;
    return new Promise(resolve => { done = resolve; });
  } });
  const running = state.calculate();
  await state.calculate();
  state.sample('pending');
  assert.equal(signal.aborted, true);
  done({ final_equity: 'old' });
  await running;
  assert.equal(state.report.value, null);
  assert.equal(state.input.events[0].payment_date, '2026-01-09');
  state.dispose();
});

test('sample presets distinguish pending, sold and assumed-tax cases', () => {
  const state = createDividendScenario();
  state.sample('sell');
  assert.equal(state.input.fills[0].side, 'sell');
  state.sample('tax');
  assert.equal(state.input.events[0].assumed_tax_rate, '0.20');
  assert.equal(state.input.fills.length, 0);
  state.sample('hold');
  assert.deepEqual(JSON.parse(JSON.stringify(state.input)), dividendSample());
  state.dispose();
});

test('server failure clears busy state and remains retryable', async () => {
  const state = createDividendScenario({ evaluate: async () => { throw new Error('应收不可提前使用'); } });
  await state.calculate();
  assert.equal(state.error.value, '应收不可提前使用');
  assert.equal(state.loading.value, false);
  state.dispose();
});
