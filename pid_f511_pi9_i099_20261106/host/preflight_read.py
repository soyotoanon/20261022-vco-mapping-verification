"""Read FPGA registers and the 556 nm meter without changing FPGA parameters."""
import json
import sys
import time
from bench import CONSOLE, UART, console_free, new_run, save

def main():
    out = new_run('inspection')
    console_free()
    uart = UART('COM9', out)
    try:
        state = uart.snapshot()
        telemetry = []
        for _ in range(6):
            telemetry.append(uart.live())
            time.sleep(.1)
        save(out / 'fpga.json', dict(registers=state, telemetry=telemetry, fpga_control_writes=0))
        print('FPGA', json.dumps({key: state[key] for key in ('00','01','03','05','18','19','1A','1B','1E','1F','2E','30','34','3F')}), flush=True)
    finally:
        uart.close()
    sys.path.insert(0, str(CONSOLE))
    from pm_driver import ThorlabsPowerMeter
    meter = ThorlabsPowerMeter('P2007293', 556.0)
    try:
        values = []
        for _ in range(8):
            values.append(meter.read_mw())
            time.sleep(.1)
        save(out / 'meter.json', dict(identity=meter.identity, power_mw=values))
        print('METER_MW', values, flush=True)
    finally:
        meter.close()
    print('SAVED', out, flush=True)

if __name__ == '__main__':
    main()
