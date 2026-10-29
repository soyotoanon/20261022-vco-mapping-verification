"""Local MOT UI + verified UART register adapter. No hardware is opened on startup."""
from __future__ import annotations

import argparse
import bisect
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "vendor"))
APP = "mot556-console"
VERSION = "1.3.0"


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name}必须是有限数字")
    return float(value)


class Calibration:
    def __init__(self, payload):
        if not isinstance(payload, dict) or payload.get("encoding") != "pi_unsigned_14":
            raise ValueError("需要声明 pi_unsigned_14 编码的标定")
        raw = payload.get("points")
        if not isinstance(raw, list) or not 2 <= len(raw) <= 4096:
            raise ValueError("标定需要 2–4096 个节点")
        self.points = sorted((number(p["power_mw"], "功率"), number(p["adc_code"], "ADC码")) for p in raw)
        if any(not 0 <= c <= 16383 for _, c in self.points):
            raise ValueError("ADC 码超出 14 位范围")
        self.powers = [p for p, _ in self.points]
        self.codes = [c for _, c in self.points]
        delta = self.codes[1] - self.codes[0]
        if not delta or any(self.powers[i] <= self.powers[i - 1] or (self.codes[i] - self.codes[i - 1]) * delta <= 0 for i in range(1, len(self.points))):
            raise ValueError("功率与 ADC 码必须严格单调")

    @staticmethod
    def interpolate(points, value):
        if not points[0][0] <= value <= points[-1][0]:
            raise ValueError("功率或 ADC 码超出标定范围，不能外推")
        i = min(max(1, bisect.bisect_left([x for x, _ in points], value)), len(points) - 1)
        (a, va), (b, vb) = points[i - 1:i + 1]
        return va + (value - a) * (vb - va) / (b - a)

    def to_code(self, power):
        # Match JavaScript Math.round for the positive 14-bit code domain.
        return math.floor(self.interpolate(self.points, power) + .5)

    def to_power(self, code):
        return self.interpolate(sorted((c, p) for p, c in self.points), code)


class FPGA:
    """8-byte C5 UART protocol from the existing A623/A624/5030 project.

    Connect and disconnect perform zero register writes. Stream frames are parsed
    and skipped rather than disabling the board's stream configuration.
    """
    def __init__(self, port, serial_factory=None):
        if not re.fullmatch(r"COM[1-9][0-9]{0,3}", port, flags=re.I):
            raise ValueError("请填写 Windows 串口，例如 COM9")
        if serial_factory is None:
            import serial
            serial_factory = serial.Serial
        self.port = port.upper()
        self.buffer = bytearray()
        self.serial = serial_factory(port=None, baudrate=115200, timeout=.05, write_timeout=1)
        try:
            self.serial.dtr = False
            self.serial.rts = False
            self.serial.port = self.port
            self.serial.open()
            perf = self.read(0x3f)
            piecewise = self.read(0x38)
            revision = self.read(0x2f)
            if revision != 0xD216 or not (perf in (0xA623, 0xA624) or piecewise == 0x5030):
                raise RuntimeError(f"不支持的 FPGA 固件（ID {perf:04X}/{piecewise:04X}/{revision:04X}）；未写入控制参数")
            self.firmware = f"{perf:04X}" if perf in (0xA623, 0xA624) else "5030"
            self.info = {"kp_register": self.read(0x1a), "ki_register": self.read(0x1b), "out_min": self.read(0x1e), "out_max": self.read(0x1f), "hardware_slew": self.read(0x2c)}
            self.latest = self.snapshot()
        except BaseException:
            self.serial.close()
            raise

    @staticmethod
    def frame(command, address, value=0):
        return bytes([0xC5, command, address, value & 255, value >> 8 & 255, 0x5C, 0xC5, 0xC5])

    def read(self, address):
        self.serial.write(self.frame(2, address))
        deadline = time.monotonic() + 1.4
        while time.monotonic() < deadline:
            self.buffer.extend(self.serial.read(max(1, min(4096, self.serial.in_waiting))))
            while len(self.buffer) >= 8:
                if self.buffer[0] != 0xC5 or self.buffer[5:8] != b"\x5c\xc5\xc5":
                    del self.buffer[0]
                    continue
                frame = bytes(self.buffer[:8])
                del self.buffer[:8]
                if frame[1] == 2 and frame[2] == address:
                    return frame[3] + 256 * frame[4]
        raise TimeoutError(f"FPGA 寄存器 0x{address:02X} 未返回有效应答")

    def write_verified(self, address, value):
        if address not in (0x00, 0x18, 0x2c):
            raise ValueError("此界面只写目标、目标变化方式和 PI 使能位")
        if not isinstance(value, int) or not 0 <= value <= 65535:
            raise ValueError("寄存器值越界")
        self.serial.write(self.frame(1, address, value))
        actual = self.read(address)
        if actual != value:
            raise RuntimeError(f"寄存器 0x{address:02X} 读回 {actual}，与写入 {value} 不一致")
        if address == 0x2c:
            self.info["hardware_slew"] = actual

    def snapshot(self):
        ctrl = self.read(0)
        status = self.read(1)
        adc, dac, target, armed = [self.read(a) for a in (3, 5, 0x18, 0x2e)]
        return {"connected": True, "port": self.port, "firmware": self.firmware, "adc_code": adc, "dac_code": dac, "setpoint_code": target, "pi_enabled": ctrl & 15 == 1 and armed == 1, "ctrl": ctrl, "status": status, "armed": armed == 1, "sampled_at": time.time() * 1000, "error": "", "ramp": None, **self.info}

    def set_pi(self, enabled):
        ctrl = self.read(0)
        if enabled:
            if ctrl & 0x71E:
                raise RuntimeError("当前不是正常 PI 输出模式，请先用原控制程序核对模式")
            if self.read(0x2e) != 1:
                raise RuntimeError("板上闭环未解锁，请先用原控制程序核对方向、通道并解锁")
            if self.read(1) & 0x3f:
                raise RuntimeError("板上存在保护或限幅标志，请先在原控制程序中处理")
        self.write_verified(0, ctrl | 1 if enabled else ctrl & ~1)

    def close(self):
        self.serial.close()


class Controller:
    def __init__(self, wavelength=556):
        self.wavelength = wavelength
        self.lock = threading.RLock()
        self.fpga = None
        self.meter = None
        self.ramp_job = None
        self.closed = threading.Event()
        self.instance = str(uuid.uuid4())
        self.state = {"wavelength_nm": wavelength, "fpga": {"connected": False, "sampled_at": None, "error": ""}, "meter": {"connected": False, "sampled_at": None, "power_mw": None, "error": ""}}
        self.thread = threading.Thread(target=self.worker, name="MOT-telemetry", daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.state, allow_nan=False))

    def connect_fpga(self, port):
        with self.lock:
            if self.fpga:
                raise ValueError("FPGA 已连接，请先断开再切换串口")
            device = FPGA(port)
            self.fpga = device
            self.state["fpga"] = device.latest

    def connect_meter(self, serial):
        with self.lock:
            if self.meter:
                raise ValueError("功率计已连接，请先断开再切换设备")
            if not isinstance(serial, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", serial):
                raise ValueError("请填写仪器的有效序列号")
            from pm_driver import ThorlabsPowerMeter
            device = ThorlabsPowerMeter(serial=serial, wavelength=float(self.wavelength))
            try:
                power = device.read_mw()
                if not math.isfinite(power):
                    raise ValueError("功率计返回无效读数")
            except BaseException:
                device.close()
                raise
            self.meter = device
            self.state["meter"] = {"connected": True, "sampled_at": time.time() * 1000, "power_mw": power, "identity": device.identity, "error": ""}

    def disconnect(self, kind):
        with self.lock:
            device = self.fpga if kind == "fpga" else self.meter
            if kind == "fpga":
                self.ramp_job = None
                self.fpga = None
            else:
                self.meter = None
            try:
                if device:
                    device.close()
            finally:
                self.state[kind] = {"connected": False, "sampled_at": None, "power_mw": None, "error": ""}

    def target(self, power, rate, payload, mode="step"):
        power = number(power, "目标功率")
        if mode not in ("step", "ramp"):
            raise ValueError("目标变化方式必须为直接更新或按速率渐变")
        if power < 0:
            raise ValueError("目标必须非负")
        if mode == "ramp":
            rate = number(rate, "斜坡速率")
            if not .01 <= rate <= 100:
                raise ValueError("斜坡速率范围 0.01–100 mW/s")
        if not isinstance(payload, dict) or payload.get("wavelength_nm") != self.wavelength:
            raise ValueError(f"必须使用 {self.wavelength} nm 本路标定，不能复用其他通道标定")
        cal = Calibration(payload)
        code = cal.to_code(power)
        with self.lock:
            if self.fpga is None:
                raise RuntimeError("FPGA 未连接")
            ctrl = self.fpga.read(0)
            if ctrl & 0x71E or self.fpga.read(0x23) & 1 or (self.fpga.firmware in ("A623", "A624") and self.fpga.read(0x30) & 3):
                raise RuntimeError("检测到其他输出模式或扰动测量，请先停止原有测量，再设置光功率目标")
            current_code = self.fpga.read(0x18)
            # Check ramp start before any write. A direct step does not need the
            # previous target to lie within the newly selected calibration.
            current = cal.to_power(current_code) if mode == "ramp" else None
            self.ramp_job = None
            self.state["fpga"]["ramp"] = None
            # One explicit limiter: software for a requested slow ramp, none for
            # a direct target. Do not stack the old one-code/sample FPGA ramp.
            if self.fpga.read(0x2c) != 16383:
                self.fpga.write_verified(0x2c, 16383)
            self.fpga.info["hardware_slew"] = 16383
            self.state["fpga"]["hardware_slew"] = 16383
            self.state["fpga"]["target_mode"] = mode
            if mode == "step":
                if code != current_code:
                    self.fpga.write_verified(0x18, code)
                self.state["fpga"]["setpoint_code"] = code
                return
            if code == current_code:
                self.ramp_job = None
                self.state["fpga"]["ramp"] = None
                return
            self.ramp_job = {"target": power, "position": current, "rate": rate, "cal": cal, "last": time.monotonic(), "final_code": code, "last_code": current_code}
            self.state["fpga"]["ramp"] = {"target_mw": power, "rate_mw_s": rate, "position_mw": current}

    def set_pi(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("PI 使能必须是布尔值")
        with self.lock:
            if not self.fpga:
                raise RuntimeError("FPGA 未连接")
            # Stop a pending setpoint ramp before either PI state transition.
            self.ramp_job = None
            self.fpga.set_pi(enabled)
            self.state["fpga"] = self.fpga.snapshot()

    def advance_ramp(self):
        job = self.ramp_job
        if not job or not self.fpga:
            return
        now = time.monotonic()
        dt = max(0, min(.5, now - job["last"]))
        job["last"] = now
        distance = job["target"] - job["position"]
        job["position"] += math.copysign(min(abs(distance), job["rate"] * dt), distance)
        code = job["cal"].to_code(job["position"])
        if code != job["last_code"]:
            self.fpga.write_verified(0x18, code)
            job["last_code"] = code
        if abs(job["position"] - job["target"]) < 1e-10:
            if code != job["final_code"]:
                raise RuntimeError("斜坡终点码不一致")
            self.ramp_job = None

    def worker(self):
        while not self.closed.is_set():
            with self.lock:
                if self.fpga:
                    try:
                        self.advance_ramp()
                        snap = self.fpga.snapshot()
                        if self.ramp_job:
                            snap["ramp"] = {"target_mw": self.ramp_job["target"], "rate_mw_s": self.ramp_job["rate"], "position_mw": self.ramp_job["position"]}
                        self.state["fpga"] = snap
                    except Exception as exc:
                        self.ramp_job = None
                        try:
                            self.fpga.close()
                        except Exception:
                            pass
                        self.fpga = None
                        self.state["fpga"] = {"connected": False, "sampled_at": None, "ramp": None, "error": f"FPGA 连接中断：{exc}"}
                if self.meter:
                    try:
                        power = self.meter.read_mw()
                        if not math.isfinite(power):
                            raise ValueError("无效功率读数")
                        self.state["meter"].update(power_mw=power, sampled_at=time.time() * 1000, error="")
                    except Exception as exc:
                        try:
                            self.meter.close()
                        except Exception:
                            pass
                        self.meter = None
                        self.state["meter"] = {"connected": False, "sampled_at": None, "power_mw": None, "error": f"功率计连接中断：{exc}"}
            self.closed.wait(.2)

    def close(self):
        self.closed.set()
        self.thread.join(timeout=5)
        for kind in ("fpga", "meter"):
            try:
                self.disconnect(kind)
            except Exception as exc:
                print(f"Device close failed ({kind}): {exc}", file=sys.stderr)


class Handler(BaseHTTPRequestHandler):
    server_version = "MOTConsole/1.0"

    @property
    def controller(self):
        return self.server.controllers.get(self.channel_id, self.server.controllers["556"])

    @property
    def channel_id(self):
        return parse_qs(urlparse(self.path).query).get("channel", ["556"])[0]

    def connect_device(self, kind, identifier):
        # A physical instrument belongs to exactly one optical channel at a time.
        with self.server.connection_lock:
            for key, other in self.server.controllers.items():
                if key == self.channel_id:
                    continue
                snapshot = other.snapshot()
                device = snapshot[kind]
                assigned = device.get("port") if kind == "fpga" else device.get("identity", {}).get("serial")
                if device.get("connected") and isinstance(identifier, str) and str(assigned).upper() == identifier.upper():
                    raise ValueError(f"此设备已连接到 {key} nm 通道，请先在该通道断开")
            if kind == "fpga":
                self.controller.connect_fpga(identifier)
            else:
                self.controller.connect_meter(identifier)

    def log_message(self, fmt, *args):
        if not (args and isinstance(args[0], str) and any(p in args[0] for p in ("/api/health", "/api/state", "/api/ports", "/api/meters"))):
            super().log_message(fmt, *args)

    def host_allowed(self):
        return self.headers.get("Host", "") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

    def respond(self, status, payload):
        raw = json.dumps({"app": APP, "version": VERSION, "target_modes": ["step", "ramp"], "instance_id": self.controller.instance, **payload}, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.channel_id not in self.server.controllers:
            self.respond(400, {"error": "未知光路通道"})
            return
        if not self.host_allowed():
            self.respond(403, {"error": "仅允许本机地址访问"})
            return
        path = urlparse(self.path).path
        if path == "/api/health":
            # Availability check must not wait for a serial/VISA device lock.
            self.respond(200, {"healthy": True, "pid": os.getpid()})
            return
        if path == "/api/state":
            self.respond(200, {"state": self.controller.snapshot()})
            return
        if path == "/api/ports":
            try:
                from serial.tools import list_ports
                ports = [{"device": p.device, "description": p.description} for p in list_ports.comports()]
                self.respond(200, {"ports": ports})
            except Exception as exc:
                self.respond(503, {"error": f"串口枚举失败：{exc}"})
            return
        if path == "/api/meters":
            try:
                from pm_driver import discover_meters
                with self.server.connection_lock:
                    meters = discover_meters()
                    for channel, controller in self.server.controllers.items():
                        meter = controller.snapshot()["meter"]
                        if not meter.get("connected"):
                            continue
                        identity = meter["identity"]
                        matches = [m for m in meters if m["serial"].casefold() == identity["serial"].casefold()]
                        if not matches:
                            matches = [dict(identity)]
                            meters.extend(matches)
                        for match in matches:
                            match["connected_channel"] = channel
                    self.respond(200, {"meters": meters})
            except Exception as exc:
                self.respond(503, {"error": f"功率计扫描失败：{exc}"})
            return
        allowed = {"/": "index.html", "/index.html": "index.html", "/styles.css": "styles.css", "/core.js": "core.js", "/calibration.js": "calibration.js", "/app.js": "app.js"}
        if path not in allowed:
            self.respond(404, {"error": "未找到资源"})
            return
        file = ROOT / allowed[path]
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}[file.suffix])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.channel_id not in self.server.controllers:
            self.respond(400, {"error": "未知光路通道"})
            return
        origin = self.headers.get("Origin")
        expected = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if not self.host_allowed() or self.headers.get("X-MOT-Console") != "1" or origin is not None and origin not in expected:
            self.respond(403, {"error": "请从本机控制台发起设备操作"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2_000_000:
                raise ValueError("请求体长度无效")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("需要 JSON 对象")
            route = urlparse(self.path).path
            if route == "/api/connect/fpga":
                self.connect_device("fpga", data.get("port", ""))
            elif route == "/api/connect/meter":
                self.connect_device("meter", data.get("serial", ""))
            elif route in ("/api/disconnect/fpga", "/api/disconnect/meter"):
                self.controller.disconnect(route.rsplit("/", 1)[-1])
            elif route == "/api/target":
                self.controller.target(data.get("power_mw"), data.get("rate_mw_s"), data.get("calibration"), data.get("mode", "step"))
            elif route == "/api/pi":
                self.controller.set_pi(data.get("enabled"))
            else:
                self.respond(404, {"error": "未知操作"})
                return
            self.respond(200, {"state": self.controller.snapshot()})
        except Exception as exc:
            self.respond(400, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser(description="Local 556 nm MOT power console")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--check", action="store_true", help="Check bundled calibration without opening instruments")
    args = parser.parse_args()
    if args.check:
        cal = Calibration(json.loads((ROOT / "data" / "calibration.json").read_text(encoding="utf-8-sig")))
        import serial
        print(f"READY {VERSION}: {len(cal.points)-1} segments, 10 mW -> PI ADC {cal.to_code(10)}, UART {serial.VERSION}; no hardware opened")
        return 0
    if not 1024 <= args.port <= 65535:
        parser.error("port must be 1024..65535")
    controllers = {"556": Controller(556), "399": Controller(399)}
    try:
        # Exclusive bind fails loudly if an old process already owns the port.
        class LocalServer(ThreadingHTTPServer):
            allow_reuse_address = False
            daemon_threads = True
            def server_bind(self):
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                super().server_bind()
        server = LocalServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        for controller in controllers.values():
            controller.close()
        print(f"START FAILED: Port {args.port} is unavailable ({exc}). Close the previous console or select --port.", file=sys.stderr)
        return 1
    server.controllers = controllers
    server.connection_lock = threading.RLock()
    url = f"http://127.0.0.1:{args.port}"
    print(f"MOT PID Console {VERSION} [556 nm / 399 nm]\nLocal: {url}\nNo instruments opened. Connect from the UI. Ctrl+C to stop.", flush=True)
    if args.open:
        threading.Timer(.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for controller in controllers.values():
            controller.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
