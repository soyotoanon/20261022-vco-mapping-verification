"""Stop only this experiment's injection/capture, retaining PI and workpoint."""
import json
from datetime import datetime, timezone
from pathlib import Path
import configure_workpoint as driver

uart = driver.SerialUART("COM9")
report = dict(utc=datetime.now(timezone.utc).isoformat(), output_target_and_gains_changed=False)
try:
    if uart.read(0x3F) != 0xA624:
        raise RuntimeError("Expected A624")
    report["before"] = {hex(a): uart.read(a) for a in [0, 1, 5, 0x18, 0x19, 0x1A, 0x1B, 0x2E, 0x30, 0x31, 0x36]}
    for address, value, expected in [(0x34, 2, 0), (0x30, 0, 0), (0x31, 0, 0)]:
        uart.write(address, value)
        if uart.read(address) != expected:
            raise RuntimeError(f"Probe stop did not read back at {hex(address)}")
    report["after"] = {hex(a): uart.read(a) for a in [0, 1, 5, 0x18, 0x19, 0x1A, 0x1B, 0x2E, 0x30, 0x31, 0x36]}
    report["success"] = True
finally:
    uart.close()
    folder = Path(__file__).resolve().parents[1] / "outputs" / "workpoint_350mVpp_20261027"
    path = folder / ("probe_stop_" + datetime.now(timezone.utc).strftime("%H%M%S_%f") + ".json")
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(dict(saved=str(path), **report), indent=2))
