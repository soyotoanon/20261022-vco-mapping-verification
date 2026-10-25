"""Switch PI gains on the A624 firmware. Usage:
  python switch_candidate.py C4|C19|C20|C21|V1|V2|V3
  python switch_candidate.py <Kp> <Ki>     (Ki in 0..0.99997)
  python switch_candidate.py               (just print current)
RAM only; never touches Flash. The measurement UI must be disconnected
(not holding COM9) while this runs.
"""
import sys, time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path[:0] = [str(BASE / "host/vendor"), str(BASE / "host/performance")]
from device import Device

NAMED = {
    "C4": (12.0, 0.5), "C19": (10.5, 0.658), "C20": (14.0, 0.858), "C21": (12.0, 0.75),
    "V1": (10.5, 0.658), "V2": (14.0, 0.858), "V3": (12.0, 0.75),
}

dev = Device("COM9")
try:
    if dev.read(0x3F) != 0xA624:
        raise SystemExit("板上不是 A624 固件（掉电后需重新加载 bit 再设参数）")
    cur_kp = dev.read(0x1A)
    cur_ki = dev.read(0x1B)
    kp_now = (cur_kp & 32767) / 256 if cur_kp & 0x8000 else (0 if cur_kp >= 15 else 2 ** -cur_kp)
    ki_now = (cur_ki & 32767) / 32768 if cur_ki & 0x8000 else (0 if cur_ki >= 23 else 2 ** -cur_ki)
    print(f"当前: Kp={kp_now:g} Ki={ki_now:g}  (0x1a={cur_kp:#06x} 0x1b={cur_ki:#06x})")
    if len(sys.argv) == 1:
        print("可选: " + ", ".join(NAMED) + "  或直接给 Kp Ki")
        raise SystemExit
    if len(sys.argv) == 2 and sys.argv[1].upper() in NAMED:
        kp, ki = NAMED[sys.argv[1].upper()]
    elif len(sys.argv) == 3:
        kp, ki = float(sys.argv[1]), float(sys.argv[2])
    else:
        raise SystemExit("用法: switch_candidate.py C4|C19|C20|C21 或 <Kp> <Ki>")
    if not (0 <= kp <= 127.996 and 0 <= ki <= 0.99997):
        raise SystemExit("Kp 范围 0..127.996,Ki 范围 0..0.99997")
    regs = {0x1A: 0x8000 | round(kp * 256), 0x1B: 0x8000 | round(ki * 32768)}
    for addr in (0x1B, 0x1A):
        dev.write(addr, regs[addr])
        if dev.read(addr) != regs[addr]:
            raise SystemExit(f"读回不一致 @{hex(addr)},已停止")
    time.sleep(0.5)
    dev.require_closed_loop()
    print(f"已切换: Kp={kp:g} Ki={ki:g},闭环正常,实时ADC={dev.read(3)} 目标={dev.read(0x18)}")
finally:
    dev.close()
