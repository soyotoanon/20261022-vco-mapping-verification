"""Scan PI at an already locked F511 operating point; restore original settings.

Measures actuator-injected small-signal rejection with the in-loop ADC. This is
not an out-of-loop RIN acceptance test. Does not change the target, DAC limits,
sampling clock, source files or flash.
"""
import argparse
import json
import math
import time

import numpy as np

from bench import FS, ProbeSession, fit_tone, new_run, save, quantize_ki

def floats(value):
    values = [float(item) for item in value.split(',')]
    if not values or not all(math.isfinite(item) for item in values):
        raise argparse.ArgumentTypeError('Expected finite comma-separated numbers.')
    return values

def decode_gain(value, integral=False):
    if value & 0x8000:
        return (value & 0x7FFF) / (32768 if integral else 256)
    return 2.0**(-value)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scales', type=floats, default=[1.0])
    parser.add_argument('--gains', help='Absolute P:I pairs, comma separated; overrides --scales.')
    parser.add_argument('--frequencies', type=floats, default=[1000,10000,15000,30000,60000])
    parser.add_argument('--amplitude-dac', type=int, default=16)
    parser.add_argument('--points', type=int, choices=[2048,4096,8192], default=4096)
    args = parser.parse_args()
    if not all(.1 <= s <= 2 for s in args.scales):
        parser.error('Initial gain scaling range is 0.1 to 2.')
    if not all(100 <= f <= 1000000 for f in args.frequencies):
        parser.error('Tone frequencies must be 100 to 1000000 Hz (5 MS/s ADC).')
    amplitude_limit = 512 if min(args.frequencies)>=300000 else 128
    if not 1 <= args.amplitude_dac <= amplitude_limit:
        parser.error('Tone amplitude limit is 128 DAC codes, or 512 for frequencies >=300 kHz.')
    pairs = None
    if args.gains:
        try:
            pairs = [tuple(float(v) for v in item.split(':')) for item in args.gains.split(',')]
            if any(len(pair) != 2 or not all(math.isfinite(v) for v in pair)
                   or not 0 <= pair[0] <= 30 or not 0 <= pair[1] < 1 for pair in pairs):
                raise ValueError()
        except ValueError:
            parser.error('Expected P:I pairs with 0<=P<=30 and 0<=I<1.')
    out = new_run('pi_scan')
    rows = []
    save(out / 'request.json', vars(args) | {'measurement': 'In-loop actuator-injected rejection',
         'out_of_loop_noise_verified': False, 'target_unchanged': True})
    with ProbeSession(out) as board:
        kp0 = decode_gain(board.original['1A'])
        ki0 = decode_gain(board.original['1B'], integral=True)
        trials = pairs or [(kp0 * scale, ki0 * scale) for scale in args.scales]
        for index, (p_requested, i_requested) in enumerate(trials):
            kp = round(p_requested * 256) / 256
            ki = quantize_ki(i_requested)
            board.gains(kp, ki)
            trial = dict(index=index, kp=kp, ki=ki, points=[])
            rows.append(trial)
            save(out / 'results.json', rows)
            for frequency in args.frequencies:
                decim = max(0, min(12, int(np.floor(np.log2(FS/(20*frequency))))))
                pair = dict(requested_hz=frequency, amplitude_dac_peak=args.amplitude_dac, states={})
                for held in ([True, False] if index % 2 == 0 else [False, True]):
                    state = 'held' if held else 'closed'
                    name = f'trial{index:02d}_{frequency:g}hz_{state}'
                    try:
                        actual = board.tone(frequency, args.amplitude_dac, held)
                        time.sleep(.08)
                        raw, meta = board.capture(decim, args.points)
                    finally:
                        board.quiet()
                    meta.update(frequency_hz=actual, kp=kp, ki=ki, state=state,
                                amplitude_dac_peak=args.amplitude_dac)
                    np.savez_compressed(out / (name + '.npz'), raw=raw)
                    save(out / (name + '.json'), meta)
                    pair['states'][state] = fit_tone(raw, meta, actual)
                a0 = pair['states']['held']['amplitude_peak_adc_codes']
                a1 = pair['states']['closed']['amplitude_peak_adc_codes']
                if a0 <= 0 or a1 <= 0:
                    raise RuntimeError('Non-positive fitted tone amplitude.')
                pair['closed_over_held_db'] = float(20*np.log10(a1/a0))
                pair['plant_adc_codes_per_dac_code'] = a0 / args.amplitude_dac
                pair['held_tone_resolved'] = bool(a0 > 5 * pair['states']['held']['residual_rms_adc'] * np.sqrt(2/args.points))
                trial['points'].append(pair)
                save(out / 'results.json', rows)
                print('PAIR', json.dumps(dict(kp=kp,ki=ki,frequency_hz=frequency,
                    closed_over_held_db=pair['closed_over_held_db'],held_tone_resolved=pair['held_tone_resolved'])), flush=True)
    print('SCAN_RESTORED', out, flush=True)

if __name__ == '__main__':
    main()
