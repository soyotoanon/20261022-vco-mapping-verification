"""Identify devices and read FPGA registers without changing control settings."""
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, r"D:\codex\2026-10-23\wo\outputs\pid_gain_tuning\host\vendor")
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "workpoint_350mVpp_20261027"
OUT.mkdir(parents=True, exist_ok=True)


def read_register(port, addr):
    port.write(bytes([197, 2, addr, 0, 0, 92, 197, 197]))
    packet = bytearray()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        packet.extend(port.read(max(1, min(port.in_waiting, 512))))
        while len(packet) >= 8:
            if packet[0] == 197 and packet[5:8] == bytes([92, 197, 197]):
                frame = bytes(packet[:8])
                del packet[:8]
                if frame[1] == 2 and frame[2] == addr:
                    return frame[3] | frame[4] << 8
            else:
                del packet[0]
    raise TimeoutError(f"No read reply for register 0x{addr:02X}")


def main():
    now = datetime.now(timezone(timedelta(hours=8)))
    report = dict(local_time=now.isoformat(), register_writes=False,
                  output_changes=False, ports=[], instruments=[], errors=[])
    try:
        import serial
        from serial.tools import list_ports
        report["ports"] = [dict(device=p.device, description=p.description, hwid=p.hwid)
                           for p in list_ports.comports()]
        if any(p["device"].upper() == "COM9" for p in report["ports"]):
            port = serial.Serial(port=None, baudrate=115200, timeout=.05, write_timeout=2)
            port.dtr = False
            port.rts = False
            port.port = "COM9"
            try:
                port.open()
                version = read_register(port, 0x3F)
                report["fpga"] = dict(port="COM9", firmware_hex=f"0x{version:04X}")
                if version not in (0xA623, 0xA624):
                    raise RuntimeError("Unrecognized FPGA firmware; no further requests")
                addresses = list(range(0x34)) + [0x35, 0x36, 0x38, 0x3C, 0x3F]
                report["fpga"]["registers"] = {
                    f"0x{addr:02X}": read_register(port, addr) for addr in addresses}
            except Exception as exc:
                report["errors"].append(dict(stage="fpga_read", error=repr(exc)))
            finally:
                if port.is_open:
                    port.close()
    except Exception as exc:
        report["errors"].append(dict(stage="serial_enumeration", error=repr(exc)))
    try:
        import pyvisa
        rm = pyvisa.ResourceManager()
        try:
            report["visa_library"] = str(rm.visalib)
            report["visa_resources"] = list(rm.list_resources())
            for name in report["visa_resources"]:
                if not name.upper().startswith("USB"):
                    continue
                item = dict(resource=name)
                try:
                    with rm.open_resource(name, open_timeout=2000) as inst:
                        inst.timeout = 1800
                        item["identity"] = inst.query("*IDN?").strip()
                        if "DS1102Z-E" in item["identity"]:
                            for command in [":TRIG:STAT?", ":ACQ:SRAT?", ":TIM:SCAL?",
                                            ":CHAN1:DISP?", ":CHAN2:DISP?",
                                            ":CHAN1:SCAL?", ":CHAN2:SCAL?",
                                            ":CHAN1:PROB?", ":CHAN2:PROB?",
                                            ":MEAS:VPP? CHAN1", ":MEAS:VAV? CHAN1",
                                            ":MEAS:VPP? CHAN2", ":MEAS:VAV? CHAN2"]:
                                try:
                                    item[command] = inst.query(command).strip()
                                except Exception as exc:
                                    item[command] = dict(error=repr(exc))
                except Exception as exc:
                    item["error"] = repr(exc)
                report["instruments"].append(item)
        finally:
            rm.close()
    except Exception as exc:
        report["errors"].append(dict(stage="visa_enumeration", error=repr(exc)))
    path = OUT / ("read_only_probe_" + now.strftime("%H%M%S_%f") + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(saved=str(path), **report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
