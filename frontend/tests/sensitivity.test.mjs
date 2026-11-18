import assert from 'node:assert/strict';
import test from 'node:test';
import { createSensitivity, parseSensitivityIds } from '../src/composables/sensitivity.ts';
import { evaluateSensitivity } from '../src/sensitivityApi.ts';

test('sensitivity is manual and includes declared axes, baseline and retrospective notes', async () => {
  let calls = 0;
  const state = createSensitivity({ evaluate: async payload => {
    calls++;
    assert.deepEqual(payload.run_ids, [3, 2, 1]);
    assert.equal(payload.baseline_run_id, 3);
    assert.deepEqual(payload.axes, ['top_n', 'rebalance_interval']);
    assert.equal(payload.hypothesis, '原假设');
    return { ranked: false, cells: [{ status: 'missing' }] };
  } });
  state.useSelection([3, 2, 1]);
  state.input.secondAxis = 'rebalance_interval';
  state.input.hypothesis = '原假设';
  assert.equal(calls, 0);
  await state.calculate();
  assert.equal(state.report.value.cells[0].status, 'missing');
  state.input.stop = '更改说明';
  assert.equal(state.report.value, null);
  state.dispose();
});

test('pending comparison is single flight, aborts on input change and ignores late success', async () => {
  let resolve, signal, calls = 0;
  const state = createSensitivity({ evaluate: (_, value) => {
    calls++; signal = value;
    return new Promise(done => { resolve = done; });
  } });
  state.useSelection([1, 2]);
  const pending = state.calculate();
  await state.calculate();
  assert.equal(calls, 1);
  state.input.axis = 'commission_rate';
  assert.equal(signal.aborted, true);
  resolve({ contract_sha256: 'stale' });
  await pending;
  assert.equal(state.report.value, null);
  assert.equal(state.loading.value, false);
  state.dispose();
});

test('unmount aborts and ignores late transport failure', async () => {
  let reject;
  const state = createSensitivity({ evaluate: () => new Promise((_, fail) => { reject = fail; }) });
  state.useSelection([1, 2]);
  const pending = state.calculate();
  state.dispose();
  reject(new Error('迟到错误'));
  await pending;
  assert.equal(state.error.value, '');
});

test('invalid selection fails before HTTP and server error remains retryable', async () => {
  for (const text of ['', '1', '1,1', '1,2.5', '1,true', '1,9007199254740992'])
    assert.throws(() => parseSensitivityIds(text));
  assert.deepEqual(parseSensitivityIds('1，2 3'), [1, 2, 3]);
  const state = createSensitivity({ evaluate: async () => { throw new Error('研究库忙'); } });
  state.useSelection([1, 2]);
  state.input.baseline = '9';
  await state.calculate();
  assert.match(state.error.value, /基准/);
  state.input.baseline = '1'; state.input.secondAxis = 'top_n';
  await state.calculate();
  assert.match(state.error.value, /扫描轴/);
  state.input.secondAxis = '';
  await state.calculate();
  assert.equal(state.error.value, '研究库忙');
  assert.equal(state.loading.value, false);
  state.dispose();
});

test('API posts bounded selection and preserves structured validation and conflict errors', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(url, '/api/v1/backtests/runs/sensitivity');
      assert.equal(options.method, 'POST');
      assert.deepEqual(JSON.parse(options.body).run_ids, [1, 2]);
      return new Response(JSON.stringify({ detail: [{ msg: '超过上限' }] }), { status: 422 });
    };
    await assert.rejects(evaluateSensitivity({ run_ids: [1, 2] }, new AbortController().signal), /超过上限/);
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: '研究库忙' }), { status: 409 });
    await assert.rejects(evaluateSensitivity({}, new AbortController().signal), /研究库忙/);
  } finally { globalThis.fetch = original; }
});
