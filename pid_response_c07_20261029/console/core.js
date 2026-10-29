(function (root) {
  'use strict';
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  function normalizeCalibration(input) {
    let points;
    if (Array.isArray(input.points)) {
      points = input.points.map(p => ({ power_mw: p.power_mw, adc_code: p.adc_code }));
    } else if (Array.isArray(input.piecewise_code_nodes) && Array.isArray(input.piecewise_power_mw_nodes)) {
      if (input.piecewise_code_nodes.length !== input.piecewise_power_mw_nodes.length) throw Error('标定节点数量不一致');
      points = input.piecewise_code_nodes.map((c, i) => ({ power_mw: input.piecewise_power_mw_nodes[i], adc_code: 16383 - c }));
    } else throw Error('标定文件需要 points，或原有的两组 piecewise 节点');
    if (points.length < 2 || points.length > 4096) throw Error('标定需要 2–4096 个节点');
    if (points.some(p => !finite(p.power_mw) || !finite(p.adc_code) || p.adc_code < 0 || p.adc_code > 16383)) throw Error('标定中存在无效功率或超出 14 位范围的 ADC 码');
    points.sort((a, b) => a.power_mw - b.power_mw);
    const direction = Math.sign(points[1].adc_code - points[0].adc_code);
    if (!direction || points.some((p, i) => i && (p.power_mw <= points[i - 1].power_mw || Math.sign(p.adc_code - points[i - 1].adc_code) !== direction))) throw Error('功率与 ADC 码必须严格单调，不能重复或折返');
    if (points[points.length - 1].power_mw <= 0) throw Error('标定必须包含正功率');
    const encoding = input.encoding || (input.piecewise_code_nodes ? 'pi_unsigned_14' : null);
    if (encoding !== 'pi_unsigned_14') throw Error('points 标定必须声明 encoding: pi_unsigned_14（PI 内部 14 位无符号码）');
    return { name: String(input.name || '导入的功率标定').slice(0, 100), encoding, wavelength_nm: input.wavelength_nm ?? null, source: String(input.source || input.source_session || '用户导入').slice(0, 250), historical: !!input.historical, points };
  }
  function interpolate(points, value, x, y) {
    if (!finite(value)) throw Error('请输入有效数字');
    const sorted = [...points].sort((a, b) => a[x] - b[x]);
    if (value < sorted[0][x] || value > sorted[sorted.length - 1][x]) throw Error('超出标定范围，不能外推');
    for (let i = 1; i < sorted.length; i++) {
      if (value <= sorted[i][x]) {
        const a = sorted[i - 1], b = sorted[i];
        return a[y] + (value - a[x]) * (b[y] - a[y]) / (b[x] - a[x]);
      }
    }
    return sorted[sorted.length - 1][y];
  }
  const toCode = (cal, power) => Math.round(interpolate(cal.points, power, 'power_mw', 'adc_code'));
  const toPower = (cal, code) => interpolate(cal.points, code, 'adc_code', 'power_mw');
  function estimateRange({ reference, efficiency, minimum, maximum, margin }) {
    if (![reference, efficiency, minimum, maximum, margin].every(finite) || reference <= 0 || minimum <= 0 || minimum >= maximum || maximum > 100 || efficiency < minimum || efficiency > maximum || margin < 0 || margin >= 50) throw Error('请检查参考功率、效率与余量；参考效率必须在最小和最大效率之间');
    const low = reference * minimum / efficiency, high = reference * maximum / efficiency;
    return { low, high, recommendedLow: low + (high - low) * margin / 100, recommendedHigh: high - (high - low) * margin / 100 };
  }
  function ramp(current, target, rate, seconds) {
    if (![current, target, rate, seconds].every(finite) || rate <= 0 || seconds < 0) throw Error('斜坡速率必须大于 0');
    return current + Math.sign(target - current) * Math.min(Math.abs(target - current), rate * seconds);
  }
  function stats(values) {
    const a = values.filter(finite);
    if (!a.length) return { mean: null, sigma: null, rms: null, pp: null, count: 0 };
    const mean = a.reduce((s, v) => s + v, 0) / a.length;
    // RMS fluctuations about this instrument's own mean, normalized by that mean.
    const sigma = a.length >= 2 ? Math.sqrt(a.reduce((s, v) => s + (v - mean) ** 2, 0) / a.length) : null;
    return { mean, sigma, rms: finite(sigma) && mean > 0 ? sigma / mean * 100 : null, pp: Math.max(...a) - Math.min(...a), count: a.length };
  }
  function windowStats(history, key, end, seconds, since = 0) {
    const start = Math.max(end - seconds * 1000, since);
    const points = history.filter(p => finite(p[key]) && finite(p[key + 'Time']) && p[key + 'Time'] >= start && p[key + 'Time'] <= end);
    const result = stats(points.map(p => p[key]));
    return { ...result, duration: points.length > 1 ? (points.at(-1)[key + 'Time'] - points[0][key + 'Time']) / 1000 : 0 };
  }
  function parseCalibration(text, filename) {
    if (/\.csv$/i.test(filename)) {
      const rows = text.trim().replace(/^\uFEFF/, '').split(/\r?\n/).map(r => r.split(',').map(s => s.trim()));
      if (rows[0].join(',') !== 'power_mw,adc_code') throw Error('CSV 表头应为 power_mw,adc_code，ADC 需为 PI 内部码');
      return normalizeCalibration({ name: filename, encoding: 'pi_unsigned_14', points: rows.slice(1).map(r => ({ power_mw: Number(r[0]), adc_code: Number(r[1]) })) });
    }
    return normalizeCalibration(JSON.parse(text));
  }
  const efficiency556 = [{"rf":300.0,"eta":43.720778576},{"rf":301.0,"eta":44.199880424},{"rf":302.0,"eta":44.397249904},{"rf":303.0,"eta":44.5000346993},{"rf":304.0,"eta":44.7482864318},{"rf":305.0,"eta":45.0874195733},{"rf":306.0,"eta":45.5865115525},{"rf":307.0,"eta":45.9824408511},{"rf":308.0,"eta":46.4099235703},{"rf":309.0,"eta":46.7162940584},{"rf":310.0,"eta":46.9235452383},{"rf":311.0,"eta":47.0761919959},{"rf":312.0,"eta":47.264473335},{"rf":313.0,"eta":47.5393281909},{"rf":314.0,"eta":47.7885435309},{"rf":315.0,"eta":48.2598420479},{"rf":316.0,"eta":48.7633930734},{"rf":317.0,"eta":49.1928974788},{"rf":318.0,"eta":50.3723718986},{"rf":319.0,"eta":50.8176530143},{"rf":320.0,"eta":51.1573151953},{"rf":321.0,"eta":51.4300538937},{"rf":322.0,"eta":51.6615464142},{"rf":323.0,"eta":51.9079087203},{"rf":324.0,"eta":52.0963978963},{"rf":325.0,"eta":52.2617415991},{"rf":326.0,"eta":52.4616995932},{"rf":327.0,"eta":52.7031116022},{"rf":328.0,"eta":53.0424336864},{"rf":329.0,"eta":53.3352947758},{"rf":330.0,"eta":53.8710416268},{"rf":331.0,"eta":54.3099742699},{"rf":332.0,"eta":54.7353408316},{"rf":333.0,"eta":55.1388089415},{"rf":334.0,"eta":55.6303810036},{"rf":335.0,"eta":56.0781183737},{"rf":336.0,"eta":56.444667092},{"rf":337.0,"eta":56.7425729499},{"rf":338.0,"eta":56.9283224576},{"rf":339.0,"eta":57.047583051},{"rf":340.0,"eta":57.3397261583},{"rf":341.0,"eta":57.5265337449},{"rf":342.0,"eta":57.7424951805},{"rf":343.0,"eta":57.9698687514},{"rf":344.0,"eta":58.1989239119},{"rf":345.0,"eta":58.4505955061},{"rf":346.0,"eta":58.7627665332},{"rf":347.0,"eta":59.1787993285},{"rf":348.0,"eta":59.7115608858},{"rf":349.0,"eta":60.1766242958},{"rf":350.0,"eta":60.5821140919},{"rf":351.0,"eta":61.0149060993},{"rf":352.0,"eta":61.4632858743},{"rf":353.0,"eta":61.8272460785},{"rf":354.0,"eta":62.063178751},{"rf":355.0,"eta":62.3028902763},{"rf":356.0,"eta":62.4135728738},{"rf":357.0,"eta":63.4952316897},{"rf":358.0,"eta":63.5884559875},{"rf":359.0,"eta":63.6962477626},{"rf":360.0,"eta":63.8496125022},{"rf":361.0,"eta":64.0823709383},{"rf":362.0,"eta":64.3186248133},{"rf":363.0,"eta":64.7477513335},{"rf":364.0,"eta":65.2441036444},{"rf":365.0,"eta":65.682715085},{"rf":366.0,"eta":66.1016198084},{"rf":367.0,"eta":66.6116326721},{"rf":368.0,"eta":67.028496815},{"rf":369.0,"eta":67.4500656297},{"rf":370.0,"eta":67.7267532292},{"rf":371.0,"eta":67.9207595303},{"rf":372.0,"eta":67.9883821005},{"rf":373.0,"eta":68.0519424041},{"rf":374.0,"eta":68.1486432464},{"rf":375.0,"eta":68.2507289539},{"rf":376.0,"eta":68.4653111082},{"rf":377.0,"eta":68.7128259644},{"rf":378.0,"eta":68.9989984844},{"rf":379.0,"eta":69.3267383849},{"rf":380.0,"eta":69.7134850713},{"rf":381.0,"eta":70.1644155719},{"rf":382.0,"eta":70.7923286419},{"rf":383.0,"eta":71.2182431372},{"rf":384.0,"eta":71.5986224567},{"rf":385.0,"eta":71.8234641963},{"rf":386.0,"eta":72.1840045388},{"rf":387.0,"eta":72.3536183452},{"rf":388.0,"eta":72.5463398363},{"rf":389.0,"eta":72.5883984676},{"rf":390.0,"eta":72.6475941962},{"rf":391.0,"eta":72.7549891918},{"rf":392.0,"eta":72.8521434964},{"rf":393.0,"eta":72.9696091347},{"rf":394.0,"eta":73.2940992218},{"rf":395.0,"eta":73.5383831588},{"rf":396.0,"eta":74.0816688211},{"rf":397.0,"eta":74.4940361293},{"rf":398.0,"eta":74.8715813092},{"rf":399.0,"eta":75.2423245541},{"rf":400.0,"eta":75.4174554856},{"rf":401.0,"eta":74.7112634805},{"rf":402.0,"eta":74.8933663954},{"rf":403.0,"eta":74.9995710524},{"rf":404.0,"eta":75.0162924759},{"rf":405.0,"eta":74.9803744803},{"rf":406.0,"eta":74.975839857},{"rf":407.0,"eta":75.0238501814},{"rf":408.0,"eta":75.0595225515},{"rf":409.0,"eta":75.2748415822},{"rf":410.0,"eta":75.4127508139},{"rf":411.0,"eta":75.8588821715},{"rf":412.0,"eta":76.2261866604},{"rf":413.0,"eta":76.594039083},{"rf":414.0,"eta":76.9329077048},{"rf":415.0,"eta":77.1828599211},{"rf":416.0,"eta":77.4277862631},{"rf":417.0,"eta":77.5986281967},{"rf":418.0,"eta":77.6233985766},{"rf":419.0,"eta":77.5635037603},{"rf":420.0,"eta":77.4609456962},{"rf":421.0,"eta":77.3094325945},{"rf":422.0,"eta":77.2305301487},{"rf":423.0,"eta":77.2577378886},{"rf":424.0,"eta":77.2669582894},{"rf":425.0,"eta":77.4851114596},{"rf":426.0,"eta":77.8541542208},{"rf":427.0,"eta":78.2010151162},{"rf":428.0,"eta":78.5091616651},{"rf":429.0,"eta":78.7730956366},{"rf":430.0,"eta":78.974432912},{"rf":431.0,"eta":79.0307189239},{"rf":432.0,"eta":79.0318147913},{"rf":433.0,"eta":78.9674798229},{"rf":434.0,"eta":78.8376006533},{"rf":435.0,"eta":78.7053597007},{"rf":436.0,"eta":78.6728993555},{"rf":437.0,"eta":78.646012818},{"rf":438.0,"eta":78.7108012487},{"rf":439.0,"eta":78.8530183726},{"rf":440.0,"eta":78.9986931468},{"rf":441.0,"eta":79.2436761716},{"rf":442.0,"eta":79.6594633415},{"rf":443.0,"eta":79.8842295041},{"rf":444.0,"eta":80.0699790118},{"rf":445.0,"eta":80.1342195089},{"rf":446.0,"eta":80.0904792881},{"rf":447.0,"eta":80.0274669182},{"rf":448.0,"eta":80.3269221057},{"rf":449.0,"eta":80.2510616364},{"rf":450.0,"eta":80.1997259216},{"rf":451.0,"eta":80.205772086},{"rf":452.0,"eta":80.3037955268},{"rf":453.0,"eta":80.4759789531},{"rf":454.0,"eta":80.7405553295},{"rf":455.0,"eta":80.8864568348},{"rf":456.0,"eta":81.108332175},{"rf":457.0,"eta":81.2983328921},{"rf":458.0,"eta":81.2938360573},{"rf":459.0,"eta":81.1530548975},{"rf":460.0,"eta":80.992529232},{"rf":461.0,"eta":80.9056911954},{"rf":462.0,"eta":80.8609306844},{"rf":463.0,"eta":80.8960551209},{"rf":464.0,"eta":81.0017118442},{"rf":465.0,"eta":81.2238139156},{"rf":466.0,"eta":81.4410412669},{"rf":467.0,"eta":81.6205934561},{"rf":468.0,"eta":81.8819578077},{"rf":469.0,"eta":82.0044871086},{"rf":470.0,"eta":82.0672727473},{"rf":471.0,"eta":82.0903426435},{"rf":472.0,"eta":81.9301381804},{"rf":473.0,"eta":81.8149398539},{"rf":474.0,"eta":81.7784928189},{"rf":475.0,"eta":81.8206837101},{"rf":476.0,"eta":81.89473033},{"rf":477.0,"eta":82.0634183175},{"rf":478.0,"eta":82.206750203},{"rf":479.0,"eta":82.3896655711},{"rf":480.0,"eta":82.5825382163},{"rf":481.0,"eta":82.6691873103},{"rf":482.0,"eta":82.7135321475},{"rf":483.0,"eta":82.7234327417},{"rf":484.0,"eta":82.7019310695},{"rf":485.0,"eta":82.6614784506},{"rf":486.0,"eta":82.6064394601},{"rf":487.0,"eta":82.5905493842},{"rf":488.0,"eta":82.6470621273},{"rf":489.0,"eta":82.6911235506},{"rf":490.0,"eta":82.7528322163},{"rf":491.0,"eta":82.786331746},{"rf":492.0,"eta":82.930702816},{"rf":493.0,"eta":82.8074933215},{"rf":494.0,"eta":82.6990402471},{"rf":495.0,"eta":82.6834335852},{"rf":496.0,"eta":82.6435100057},{"rf":497.0,"eta":82.5686698267},{"rf":498.0,"eta":82.4904853629},{"rf":499.0,"eta":82.3990182317},{"rf":500.0,"eta":82.4186682661},{"rf":501.0,"eta":82.4756533658},{"rf":502.0,"eta":82.5140465099},{"rf":503.0,"eta":82.4824930893},{"rf":504.0,"eta":82.573128873},{"rf":505.0,"eta":82.591607463},{"rf":506.0,"eta":82.5428602623},{"rf":507.0,"eta":82.3401814942},{"rf":508.0,"eta":82.175744716},{"rf":509.0,"eta":82.0402917386},{"rf":510.0,"eta":81.9604445796},{"rf":511.0,"eta":81.9857062104},{"rf":512.0,"eta":82.0513259887},{"rf":513.0,"eta":82.0409341436},{"rf":514.0,"eta":82.0349446619},{"rf":515.0,"eta":82.0471881449},{"rf":516.0,"eta":82.0195269426},{"rf":517.0,"eta":81.9286077451},{"rf":518.0,"eta":81.7879021623},{"rf":519.0,"eta":81.6217460062},{"rf":520.0,"eta":81.428230956},{"rf":521.0,"eta":81.2524954081},{"rf":522.0,"eta":81.2327886909},{"rf":523.0,"eta":81.2310882072},{"rf":524.0,"eta":81.2974826503},{"rf":525.0,"eta":81.2569166658},{"rf":526.0,"eta":81.3361403141},{"rf":527.0,"eta":81.093292341},{"rf":528.0,"eta":80.9555153691},{"rf":529.0,"eta":80.8223297034},{"rf":530.0,"eta":80.7336211347},{"rf":531.0,"eta":80.6595556205},{"rf":532.0,"eta":80.6622197117},{"rf":533.0,"eta":80.5447729677},{"rf":534.0,"eta":80.5122181511},{"rf":535.0,"eta":80.4636787873},{"rf":536.0,"eta":80.4083563828},{"rf":537.0,"eta":80.3576630729},{"rf":538.0,"eta":80.2454311458},{"rf":539.0,"eta":80.0598138979},{"rf":540.0,"eta":79.860158212},{"rf":541.0,"eta":79.6279477094},{"rf":542.0,"eta":79.3741221691},{"rf":543.0,"eta":79.3854209389},{"rf":544.0,"eta":79.3140761987},{"rf":545.0,"eta":79.2174509334},{"rf":546.0,"eta":79.1602957853},{"rf":547.0,"eta":79.0796172787},{"rf":548.0,"eta":78.9972571827},{"rf":549.0,"eta":78.856343763},{"rf":550.0,"eta":78.6691205027}];

  // DDS GUI amplitude is a peak parameter; measured peak = setting * 0.77.
  // RF Vpp uses the factor 2 exactly once. No extrapolation of optical data.
  function opticsCoverage556(f) {
    if (!f || ![f.dac_code, f.out_min, f.out_max].every(finite) ||
        f.out_min < 0 || f.out_max > 16383 || f.out_min >= f.out_max ||
        f.dac_code < f.out_min || f.dac_code > f.out_max) throw Error('等待有效 DAC 与限幅读回');
    const rfAt = d => 2000 * .77 * (.226 + .0087 * (d - 8198) * .0006533);
    const dacAt = rf => 8198 + (rf / (2000 * .77) - .226) / (.0087 * .0006533);
    const calibratedMin = Math.ceil(dacAt(efficiency556[0].rf));
    const calibratedMax = Math.floor(dacAt(efficiency556.at(-1).rf));
    const lowDac = Math.max(f.out_min, calibratedMin), highDac = Math.min(f.out_max, calibratedMax);
    if (lowDac >= highDac) throw Error('DAC 可调区间不在效率标定覆盖范围内');
    const rf = rfAt(f.dac_code), lowRF = rfAt(lowDac), highRF = rfAt(highDac);
    return { rf, lowDac, highDac, lowRF, highRF, calibrated: rf >= efficiency556[0].rf && rf <= efficiency556.at(-1).rf,
      partial: lowDac !== f.out_min || highDac !== f.out_max };
  }
  function estimateOptics556(f) {
    const coverage = opticsCoverage556(f), { rf, lowRF, highRF } = coverage;
    if (!coverage.calibrated) throw Error(`当前 RF ≈ ${rf.toFixed(1)} mVpp，超出效率标定 300–550 mVpp`);
    const efficiency = interpolate(efficiency556, rf, 'rf', 'eta');
    const eta = [interpolate(efficiency556, lowRF, 'rf', 'eta'), interpolate(efficiency556, highRF, 'rf', 'eta'),
      ...efficiency556.filter(p => p.rf > lowRF && p.rf < highRF).map(p => p.eta)];
    return { ...coverage, efficiency, minimum: Math.min(...eta), maximum: Math.max(...eta) };
  }
  const api = { opticsCoverage556, estimateOptics556, finite, normalizeCalibration, interpolate, toCode, toPower, estimateRange, ramp, stats, windowStats, parseCalibration };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.MotCore = api;
})(typeof window !== 'undefined' ? window : globalThis);
