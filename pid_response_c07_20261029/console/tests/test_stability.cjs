const assert = require('node:assert/strict');
const C = require('../core.js');
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-10, `${a} != ${b}`);

// Normalize by each measured mean, never by a target or another instrument.
const s = C.stats([9, 11], 100);
near(s.mean, 10); near(s.sigma, 1); near(s.rms, 10);
near(C.stats([90, 110]).rms, s.rms);
near(C.stats([5, 5, 5]).rms, 0);
near(C.stats([8, 8, 8]).rms, 0);
assert.equal(C.stats([]).rms, null);
assert.equal(C.stats([5]).rms, null);
assert.equal(C.stats([0, 0]).rms, null);
assert.equal(C.stats([-1, 1]).rms, null);
assert.equal(C.stats([null, NaN, Infinity, 9, 11]).count, 2);
near(C.stats([null, NaN, Infinity, 9, 11]).rms, 10);

const h = [
  {power: 2, powerTime: 1000, adc: null, adcTime: null},
  {power: 9, powerTime: 11000, adc: 19, adcTime: 10900},
  {power: 11, powerTime: 12000, adc: 21, adcTime: 11900},
  {power: 100, powerTime: 14000, adc: null, adcTime: null},
];
// Old target, out-of-window data, missing values and future timestamps are excluded.
const p = C.windowStats(h, 'power', 12500, 30, 10000);
near(p.mean, 10); near(p.rms, 10); near(p.duration, 1); assert.equal(p.count, 2);
const f = C.windowStats(h, 'adc', 12500, 30, 10000);
near(f.mean, 20); near(f.rms, 5); near(f.duration, 1); assert.equal(f.count, 2);
assert.equal(C.windowStats(h, 'power', 12500, 1, 0).rms, null);
assert.equal(C.windowStats(h, 'power', 12500, 30, 13000).count, 0);
assert.equal(C.windowStats([], 'adc', 12500, 60).rms, null);
console.log('PASS: independent mean-normalized RMS, insufficient/zero-mean data, timestamps, source isolation and post-target window');
