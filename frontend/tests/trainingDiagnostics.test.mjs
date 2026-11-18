import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc';
import { createTrainingDiagnostics } from '../src/composables/trainingDiagnostics.ts';
import { fetchTrainingDiagnostics } from '../src/trainingDiagnosticsApi.ts';

const deferred = () => { let resolve; const promise = new Promise(value => { resolve = value; }); return { promise, resolve }; };

test('training diagnostics are manual, single flight and cancel stale runs', async () => {
  const pending = deferred();
  let calls = 0, signal;
  const state = createTrainingDiagnostics({ fetch: async (id, value) => { calls++; signal = value; return pending.promise; } });
  state.reset(1);
  assert.equal(calls, 0);
  const loading = state.load();
  await state.load();
  assert.equal(calls, 1);
  state.reset(2);
  assert.equal(signal.aborted, true);
  pending.resolve({ run_id: 1, status: 'recorded', factors: [] });
  await loading;
  assert.equal(state.report.value, null);
  assert.equal(state.loading.value, false);
});

test('not recorded and insufficient are reports rather than transport errors', async () => {
  for (const status of ['recorded', 'insufficient', 'not_recorded', 'unsupported']) {
    const state = createTrainingDiagnostics({ fetch: async id => ({ run_id: id, status, factors: [] }) });
    state.reset(7);
    await state.load();
    assert.equal(state.report.value.status, status);
    assert.equal(state.error.value, '');
  }
});

test('API uses GET and preserves server detail', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(url, '/api/v1/backtests/runs/7/training-diagnostics');
      assert.equal(options.method, undefined);
      return new Response(JSON.stringify({ detail: 'backtest run not found' }), { status: 404 });
    };
    await assert.rejects(fetchTrainingDiagnostics(7, new AbortController().signal), /backtest run not found/);
  } finally { globalThis.fetch = original; }
});

test('panel compiles and mounts on run detail with explicit probability caveat', () => {
  const filename = new URL('../src/pages/components/TrainingDiagnosticsPanel.vue', import.meta.url);
  const source = readFileSync(filename, 'utf8');
  const { descriptor } = parse(source);
  const script = compileScript(descriptor, { id: 'training-diagnostics' });
  const template = compileTemplate({ id: 'training-diagnostics', source: descriptor.template.content, filename: filename.pathname,
    compilerOptions: { bindingMetadata: script.bindings } });
  assert.deepEqual(template.errors, []);
  assert.match(source, /p\/q 值不是未来盈利概率/);
  const page = readFileSync(new URL('../src/pages/BacktestRunPage.vue', import.meta.url), 'utf8');
  assert.match(page, /<TrainingDiagnosticsPanel/);
});
