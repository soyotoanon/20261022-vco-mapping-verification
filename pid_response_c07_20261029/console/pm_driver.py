"""Shared PM100D / PM100USB adapter using the installed vendor TLPMX.h ABI."""
import ctypes as C
import math
from pathlib import Path
import threading

SDK_LOCK = threading.RLock()


class _TLPMX:
    def __init__(self):
        if C.sizeof(C.c_void_p) != 8:
            raise RuntimeError("本程序要求 64 位 Python 和 TLPMX_64.dll")
        self.path = Path(r"C:\Program Files\IVI Foundation\VISA\Win64\Bin\TLPMX_64.dll")
        self.dll = C.WinDLL(str(self.path))
        self.session = C.c_uint32(0)
        u32, u16, i16, dbl = C.c_uint32, C.c_uint16, C.c_int16, C.c_double
        signatures = {
            "findRsrc": [u32, C.POINTER(u32)],
            "getRsrcName": [u32, u32, C.c_char_p],
            "getRsrcInfo": [u32, u32, C.c_char_p, C.c_char_p, C.c_char_p, C.POINTER(u16)],
            "init": [C.c_char_p, u16, u16, C.POINTER(u32)],
            "close": [u32], "setTimeoutValue": [u32, u32],
            "setWavelength": [u32, dbl, u16],
            "getWavelength": [u32, i16, C.POINTER(dbl), u16],
            "setPowerUnit": [u32, i16, u16],
            "getPowerUnit": [u32, C.POINTER(i16), u16],
            "measPower": [u32, C.POINTER(dbl), u16],
            "errorMessage": [u32, C.c_int32, C.c_char_p],
        }
        for name, args in signatures.items():
            fn = getattr(self.dll, "TLPMX_"+name)
            fn.argtypes, fn.restype = args, C.c_int32

    def resources(self):
        # Enumeration does not open, reset or configure any instrument.
        with SDK_LOCK:
            count = C.c_uint32()
            self.call("findRsrc", self.session, C.byref(count))
            devices = []
            for index in range(count.value):
                resource, model, number, maker = [C.create_string_buffer(1024) for _ in range(4)]
                available = C.c_uint16()
                self.call("getRsrcName", self.session, index, resource)
                self.call("getRsrcInfo", self.session, index, model, number, maker, C.byref(available))
                # TLPMX can return an anonymous USB placeholder for an open handle.
                # It is not a second selectable meter; the server adds its known identity.
                serial = number.value.decode().strip()
                if not serial or serial.casefold() in ("n/a", "unknown"):
                    continue
                devices.append(dict(resource=resource.value.decode().strip(),
                                    model=model.value.decode().strip(), serial=serial,
                                    manufacturer=maker.value.decode().strip(), available=bool(available.value)))
            return devices

    def call(self, name, *args):
        with SDK_LOCK:
            status = getattr(self.dll, "TLPMX_"+name)(*args)
            if status < 0:
                msg = C.create_string_buffer(1024)
                self.dll.TLPMX_errorMessage(self.session, status, msg)
                raise RuntimeError(f"TLPMX {name}: {status}: {msg.value.decode(errors='replace')}")
            return status


def discover_meters():
    return _TLPMX().resources()


class ThorlabsPowerMeter(_TLPMX):
    def __init__(self, serial, wavelength=556.0):
        super().__init__()
        devices = self.resources()
        matches = [d for d in devices if d["serial"].casefold() == serial.strip().casefold()]
        if len(matches) != 1:
            found = "、".join(f'{d["model"]} ({d["serial"]})' for d in devices) or "无"
            raise RuntimeError(f"未找到唯一的功率计 {serial}；当前发现：{found}。请重新扫描并选择。")
        device = matches[0]
        # Discovery availability can lag after reconnect. The actual init call
        # below is authoritative and reports an error if access is unavailable.
        try:
            self.call("init", device["resource"].encode(), 1, 0, C.byref(self.session))
            self.call("setTimeoutValue", self.session, 1500)
            self.call("setWavelength", self.session, wavelength, 1)
            self.call("setPowerUnit", self.session, 0, 1)  # 0 = W, not dBm
            unit = C.c_int16()
            self.call("getPowerUnit", self.session, C.byref(unit), 1)
            if unit.value != 0:
                raise RuntimeError("功率计单位回读不一致，需要 W")
            readback = C.c_double()
            self.call("getWavelength", self.session, 0, C.byref(readback), 1)
            if abs(readback.value-wavelength) > .1:
                raise RuntimeError("功率计波长回读不一致")
            self.identity = dict(model=device["model"], serial=device["serial"], resource=device["resource"],
                                 wavelength_nm=readback.value, unit="W", driver=str(self.path))
        except Exception:
            self.close()
            raise

    def read_mw(self):
        power = C.c_double()
        self.call("measPower", self.session, C.byref(power), 1)
        value = power.value*1000
        if not math.isfinite(value) or abs(value) > 1e6:
            raise RuntimeError("功率计无效或超量程读数")
        return value

    def close(self):
        with SDK_LOCK:
            if self.session.value:
                self.dll.TLPMX_close(self.session)
                self.session.value = 0


# Keep existing external scripts that import the old adapter name working.
PM100D = ThorlabsPowerMeter
