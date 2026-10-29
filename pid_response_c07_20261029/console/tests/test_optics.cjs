const assert = require('node:assert/strict');
const C = require('../core.js');
const f = {dac_code:15000,out_min:1400,out_max:15000};
const a = C.estimateOptics556(f);
assert.ok(Math.abs(a.rf - 407.5773169468) < 1e-8);
assert.ok(Math.abs(a.efficiency - 75.04444444519) < 1e-8);
assert.equal(a.lowDac,2710); assert.equal(a.partial,true);
const r = C.estimateRange({...a,reference:2,margin:5});
assert.ok(r.low < 2 && r.high >= 2);
assert.ok(r.recommendedLow > r.low && r.recommendedHigh < r.high);
assert.ok(Math.abs(C.estimateOptics556({...f,dac_code:8198}).rf - 348.04)<1e-8);
assert.throws(()=>C.estimateOptics556({...f,dac_code:1400}));
// Coverage remains available as recovery guidance, without extrapolating efficiency.
const outside = C.opticsCoverage556({...f,dac_code:1400});
assert.equal(outside.calibrated,false); assert.equal(outside.lowDac,2710);
assert.ok(outside.rf < outside.lowRF);
assert.equal(C.opticsCoverage556(f).calibrated,true);
assert.throws(()=>C.estimateOptics556({...f,dac_code:null}));
assert.throws(()=>C.estimateOptics556({...f,out_min:16000}));
// Changing live output limits recomputes the covered interval.
assert.equal(C.estimateOptics556({...f,out_min:4000}).lowDac,4000);
assert.equal(C.estimateOptics556({...f,out_min:4000}).partial,false);
console.log('PASS: RF units, measured-curve interpolation, DAC limits, partial coverage, no extrapolation and range margins.');
