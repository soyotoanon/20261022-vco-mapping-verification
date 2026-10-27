"""Read-only 30-second check of the final closed loop; no control writes."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import configure_workpoint as config

OUT = Path(__file__).resolve().parents[1] / 'outputs' / 'workpoint_350mVpp_20261027'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kp', type=float, required=True)
    parser.add_argument('--ki', type=float, required=True)
    args = parser.parse_args()
    output = OUT / ('final_live_check_' + datetime.now().strftime('%H%M%S_%f') + '.json')
    report = dict(started_utc=datetime.now(timezone.utc).isoformat(), control_writes=False,
                  duration_requested_seconds=30, samples=[], errors=[])
    uart = config.SerialUART('COM9')
    expected = {0:1,0x11:4,0x19:8198,0x1E:1400,0x1F:15000,0x2E:1,0x30:0,0x31:0,
                0x1A:0x8000|round(args.kp*256), 0x1B:0x8000|round(args.ki*32768)}
    try:
        start = time.monotonic()
        for index in range(31):
            values = {f'0x{a:02X}':uart.read(a) for a in [0,1,3,5,0x18,0x30,0x31,0x2E]}
            report['samples'].append(dict(elapsed_s=time.monotonic()-start, **values))
            if values['0x01'] & 0x3D or values['0x00'] != 1 or values['0x2E'] != 1 or values['0x30'] != 0 or values['0x31'] != 0:
                raise RuntimeError('Closed-loop state changed or blocking flag asserted')
            if not 1400 < values['0x05'] < 15000:
                raise RuntimeError('DAC output reached a limit')
            if abs(values['0x03'] - values['0x18']) > 30:
                raise RuntimeError('ADC error exceeded 30 codes at a readback sample')
            if index < 30:
                time.sleep(max(0, start+index+1-time.monotonic()))
        final = {f'0x{a:02X}':uart.read(a) for a in sorted(set(expected)|{0x3F,1,3,5,0x18})}
        report['final_registers'] = final
        if final['0x3F'] != 0xA624 or any(final[f'0x{a:02X}'] != value for a,value in expected.items()):
            raise RuntimeError('Final firmware or configuration differs from requested candidate')
        report['pass'] = True
    except BaseException as exc:
        report['pass'] = False
        report['errors'].append(repr(exc))
    finally:
        uart.close()
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    codes = [s['0x05'] for s in report['samples']]
    print(json.dumps(dict(saved=str(output), passed=report['pass'], observations=len(codes),
                         DAC_range=[min(codes),max(codes)] if codes else None,errors=report['errors']),ensure_ascii=False))
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
