import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc';
import { createSignalAudit } from '../src/composables/signalAudit.ts';
import { auditBacktestSignals } from '../src/signalAuditApi.ts';

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };

test('audit is manual, single flight and changes run without stale response', async () => {
  const pending = deferred();
  let calls = 0, signal;
  const page = createSignalAudit({ audit: async (id, value) => { calls++; signal = value; return pending.promise; } });
  page.reset(1);
  assert.equal(calls, 0);
  const running = page.start();
  await page.start();
  assert.equal(calls, 1);
  page.reset(2);
  assert.equal(signal.aborted, true);
  pending.resolve({ run_id: 1, status: 'passed' });
  await running;
  assert.equal(page.report.value, null);
  assert.equal(page.loading.value, false);
});

test('failed, insufficient and error reports remain distinct from transport failure', async () => {
  for (const status of ['passed', 'failed', 'insufficient', 'error']) {
    const page = createSignalAudit({ audit: async id => ({ run_id: id, status }) });
    page.reset(9);
    await page.start();
    assert.equal(page.report.value.status, status);
    assert.equal(page.error.value, '');
  }
  const page = createSignalAudit({ audit: async () => { throw new Error('已有审计正在运行'); } });
  page.reset(1);
  await page.start();
  assert.equal(page.error.value, '已有审计正在运行');
  assert.equal(page.loading.value, false);
});

test('stop waiting ignores late failure and explains server may continue', async () => {
  const pending = deferred();
  const page = createSignalAudit({ audit: () => pending.promise });
  page.reset(1);
  const running = page.start();
  page.stopWaiting();
  pending.reject(new Error('late'));
  await running;
  assert.equal(page.error.value, '');
  assert.match(page.note.value, /服务端可能仍在计算/);
});

test('API uses explicit POST and preserves server error detail', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(url, '/api/v1/backtests/runs/7/signal-audit');
      assert.equal(options.method, 'POST');
      return new Response(JSON.stringify({ detail: '回测运行不存在' }), { status: 404 });
    };
    await assert.rejects(auditBacktestSignals(7, new AbortController().signal), /回测运行不存在/);
  } finally { globalThis.fetch = original; }
});

test('audit panel compiles script and template and mounts on run detail', () => {
  const filename = new URL('../src/pages/components/SignalAuditPanel.vue', import.meta.url);
  const source = readFileSync(filename, 'utf8');
  const { descriptor } = parse(source);
  const script = compileScript(descriptor, { id: 'signal-audit' });
  const template = compileTemplate({ id: 'signal-audit', source: descriptor.template.content, filename: filename.pathname,
    compilerOptions: { bindingMetadata: script.bindings } });
  assert.deepEqual(template.errors, []);
  const page = readFileSync(new URL('../src/pages/BacktestRunPage.vue', import.meta.url), 'utf8');
  assert.match(page, /<SignalAuditPanel :key="run.id" :run-id="run.id"/);
  assert.match(source, /item.expected \?\? '无信号'/);
});
