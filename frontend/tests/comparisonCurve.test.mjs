import assert from 'node:assert/strict';
import test from 'node:test';

import { curvePolyline, normalizeEquity, sameEquityDates } from '../src/components/operations/comparisonCurve.ts';

test('normalization includes first-day return and initial-cash drawdown peak', () => {
  const curve = normalizeEquity('100', [
    { trade_date: '2026-01-05', total_equity: '90' },
    { trade_date: '2026-01-06', total_equity: '110' },
    { trade_date: '2026-01-07', total_equity: '99' },
  ]);
  assert.deepEqual(curve.returns.map(value => Math.round(value * 100)), [-10, 10, -1]);
  assert.deepEqual(curve.drawdowns.map(value => Math.round(value * 100)), [-10, 0, -10]);
  assert.equal(sameEquityDates(curve.dates, [...curve.dates]), true);
  assert.equal(sameEquityDates(curve.dates, ['2026-01-05', '2026-01-07', '2026-01-08']), false);
});

test('invalid equity and dates cannot be plotted', () => {
  const rows = [
    { trade_date: '2026-01-05', total_equity: '100' },
    { trade_date: '2026-01-06', total_equity: '100' },
  ];
  assert.deepEqual(normalizeEquity('100', rows).drawdowns, [0, 0]);
  assert.throws(() => normalizeEquity('0', rows), /初始资金/);
  assert.throws(() => normalizeEquity('100', [rows[0], { ...rows[1], total_equity: '0' }]), /非正数/);
  assert.throws(() => normalizeEquity('100', [rows[0], { ...rows[1], total_equity: 'NaN' }]), /无效值/);
  assert.throws(() => normalizeEquity('0.00000000000000000000000001', [rows[0], { ...rows[1], total_equity: '1e308' }]), /绘图比例无效/);
  assert.throws(() => normalizeEquity('100', [rows[0], { ...rows[1], trade_date: rows[0].trade_date }]), /重复或无序/);
  assert.throws(() => normalizeEquity('100', rows.slice(0, 1)), /不足两日/);
});

test('long chart sampling retains isolated peaks and troughs', () => {
  const values = Array(3000).fill(0);
  values[1234] = 2;
  values[1235] = -2;
  const points = curvePolyline(values, -2, 2).split(' ');
  assert.ok(points.length < 600);
  assert.ok(points.some(point => point.endsWith(',12.00')));
  assert.ok(points.some(point => point.endsWith(',208.00')));
});
