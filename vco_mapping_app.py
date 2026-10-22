# -*- coding: utf-8 -*-
"""
VCO 映射关系验证工具（2026-10-22）

信号发生器（RIGOL DG5352 Pro，CH1）向 DDS 第 8 路 VCO 模拟控制输入送直流电压，
频谱仪（RIGOL DSA815）读取 100 MHz 载波功率，逐点画出
“VCO 输入电压 → RF 输出幅度（Vpp, 50 Ω）”的实测曲线，
并与 DDS 软件中设置的理想映射 A(V) = b + k·V（按峰值解释为 Vpp = 2(b+kV)）对比。

接线：
  DG5352 Pro CH1  ->  DDS 第 8 路 VCO 控制输入（1 MΩ, 直流耦合, 允许 ±5 V）
  DDS 第 8 路 RF 输出 -> DSA815 频谱输入（50 Ω）

用法：
  python vco_mapping_app.py            # 图形界面
  python vco_mapping_app.py --cli      # 无界面自检扫描（结果存 outputs/）
"""
import argparse
import csv
import json
import math
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

try:
    import pyvisa
except ImportError:
    pyvisa = None

# ---------------------------------------------------------------- 仪器层 ----

CENTER_HZ = 100e6
SPAN_HZ = 20e6
RBW_HZ = 300e3
HARD_LIMIT_V = 4.5          # VCO 允许 ±5 V，留余量


def dbm_to_vpp(dbm: float) -> float:
    """50 Ω 系统：dBm -> Vpp。"""
    p_w = 1e-3 * 10.0 ** (dbm / 10.0)
    return 2.0 * math.sqrt(2.0) * math.sqrt(p_w * 50.0)


class SignalGenerator:
    """RIGOL DG 系列，输出纯直流（APPL:DC），负载设高阻。"""

    def __init__(self, resource: str, log=print):
        self.log = log
        self.rm = pyvisa.ResourceManager()
        self.inst = self.rm.open_resource(resource)
        self.inst.timeout = 5000
        self.inst.read_termination = "\n"
        self.inst.write_termination = "\n"
        self.idn = self.inst.query("*IDN?").strip()
        self.resource = resource
        self._dc_mode = None

    def prepare(self):
        self.inst.write(":OUTP1:LOAD INF")   # 高阻负载：设定值=送到 1 MΩ 的实际值
        self.inst.write(":OUTP1 OFF")

    def _try(self, cmd: str) -> bool:
        self.inst.write(cmd)
        time.sleep(0.05)
        err = self.inst.query(":SYST:ERR?").strip()
        ok = err.startswith("0") or "no error" in err.lower()
        if not ok:
            self.log(f"DG 拒绝 {cmd!r}: {err}")
        return ok

    def set_dc(self, volts: float) -> float:
        volts = max(-HARD_LIMIT_V, min(HARD_LIMIT_V, volts))
        if self._dc_mode is None:
            for mode, cmds in (
                ("appl", [f":SOUR1:APPL:DC 1,1,{volts:.4f}"]),
                ("shap", [":SOUR1:FUNC:SHAP DC", f":SOUR1:VOLT:OFFS {volts:.4f}"]),
                ("func", [":SOUR1:FUNC DC", f":SOUR1:VOLT:OFFS {volts:.4f}"]),
            ):
                if all(self._try(c) for c in cmds):
                    self._dc_mode = mode
                    self.log(f"DG 直流模式: {mode}")
                    break
            else:
                raise RuntimeError("无法让 DG 进入直流输出模式")
        else:
            if self._dc_mode == "appl":
                self._try(f":SOUR1:APPL:DC 1,1,{volts:.4f}")
            else:
                self._try(f":SOUR1:VOLT:OFFS {volts:.4f}")
        time.sleep(0.05)
        readback = float(self.inst.query(":SOUR1:VOLT:OFFS?").strip())
        if abs(readback - volts) > 0.02:
            raise RuntimeError(f"DG 偏置回读不符：设 {volts:.4f} 回读 {readback:.4f}")
        return readback

    def output_on(self):
        self.inst.write(":OUTP1 ON")

    def output_off(self):
        try:
            self.inst.write(":OUTP1 OFF")
        except Exception:
            pass

    def close(self):
        self.output_off()
        try:
            self.inst.close()
            self.rm.close()
        except Exception:
            pass


class SpectrumAnalyzer:
    """RIGOL DSA815，峰值搜索读 100 MHz 载波。"""

    def __init__(self, resource: str, log=print):
        self.log = log
        self.rm = pyvisa.ResourceManager()
        self.inst = self.rm.open_resource(resource)
        self.inst.timeout = 5000
        self.inst.read_termination = "\n"
        self.inst.write_termination = "\n"
        self.idn = self.inst.query("*IDN?").strip()
        self.resource = resource

    def prepare(self):
        self.inst.write(f":FREQ:CENT {CENTER_HZ:.0f}")
        self.inst.write(f":FREQ:SPAN {SPAN_HZ:.0f}")
        self.inst.write(f":BAND {RBW_HZ:.0f}")

    def read_peak(self):
        """读整条轨迹后在软件里找峰值。

        不用 :CALC:MARK1:MAX —— 这台 DSA815 的标记峰值搜索在某些
        状态下会停在扫宽边缘读噪底（固件 bug，实测踩过坑）。
        """
        self.inst.write(":TRAC? TRACE1")
        time.sleep(0.15)
        raw = self.inst.read_raw(65536).decode(errors="replace")
        i = raw.find(" ", 0, 15)  # 剥掉 IEEE 块头 "#N<len>"
        vals = [float(v) for v in raw[i:].replace(" ", ",").split(",") if v.strip()]
        cent = float(self.inst.query(":FREQ:CENT?").strip())
        span = float(self.inst.query(":FREQ:SPAN?").strip())
        imax = max(range(len(vals)), key=lambda k: vals[k])
        x = (cent - span / 2) + imax * span / (len(vals) - 1)
        return x, vals[imax]

    def close(self):
        try:
            self.inst.close()
            self.rm.close()
        except Exception:
            pass


def find_instruments(log=print):
    """自动识别 DG 与 DSA815 的 VISA 地址。"""
    rm = pyvisa.ResourceManager()
    dg, dsa = None, None
    for res in rm.list_resources():
        if "ASRL" in res:
            continue
        try:
            inst = rm.open_resource(res)
            inst.timeout = 800
            idn = inst.query("*IDN?").strip().upper()
            inst.close()
            if ",DG" in idn:
                dg = res
            elif "DSA815" in idn:
                dsa = res
        except Exception:
            continue
    rm.close()
    log(f"自动识别: DG={dg}  DSA={dsa}")
    return dg, dsa


# ---------------------------------------------------------------- 扫描核心 ---

def run_sweep(dg, dsa, voltages, b, k, settle, on_point=None, should_stop=None, log=print):
    """逐点扫描，返回数据行列表。on_point(row) 每点回调（UI 实时画点）。"""
    rows = []
    dg.prepare()
    dsa.prepare()
    first = True
    for v in voltages:
        if should_stop and should_stop():
            log("扫描被手动停止")
            break
        rb = dg.set_dc(v)
        if first:
            dg.output_on()
            first = False
            time.sleep(max(settle, 1.0))
        else:
            time.sleep(settle)
        mhz, dbm = dsa.read_peak()
        vpp = dbm_to_vpp(dbm)
        ideal_vpp = 2.0 * (b + k * v)
        row = {
            "v_set": round(v, 4), "v_readback": round(rb, 4),
            "marker_mhz": round(mhz / 1e6, 6), "dbm": round(dbm, 3),
            "vpp": round(vpp, 5), "ideal_vpp": round(ideal_vpp, 5),
            "residual_vpp": round(vpp - ideal_vpp, 5),
        }
        rows.append(row)
        log(f"V={v:+.3f}  {dbm:+.2f} dBm  {vpp*1000:.1f} mVpp  @{row['marker_mhz']:.3f} MHz")
        if on_point:
            on_point(row)
    dg.output_off()
    return rows


def fit_rows(rows, b, k):
    """对实测点（输入电压 -> Vpp）做直线拟合并与理想映射对比。"""
    if len(rows) < 3:
        return None
    x = np.array([r["v_set"] for r in rows])
    y = np.array([r["vpp"] for r in rows])
    slope, intercept = np.polyfit(x, y, 1)
    yfit = slope * x + intercept
    ss_res = float(np.sum((y - yfit) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    # 理想映射换算成 Vpp：斜率 2k、截距 2b
    return {
        "slope_vpp_per_v": round(float(slope), 6),
        "intercept_vpp": round(float(intercept), 6),
        "r2": round(r2, 6),
        "ideal_slope_vpp_per_v": round(2 * k, 6),
        "ideal_intercept_vpp": round(2 * b, 6),
        "slope_err_pct": round((slope - 2 * k) / (2 * k) * 100, 2) if k else None,
        "intercept_err_pct": round((intercept - 2 * b) / (2 * b) * 100, 2) if b else None,
        "n": len(rows),
    }


def save_outputs(rows, fit, b, k, outdir: Path, meta: dict):
    outdir.mkdir(parents=True, exist_ok=True)
    csv_path = outdir / "vco_mapping_sweep.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    summary = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mapping_setting": {"b_peak_V": b, "k_V_per_V": k,
                            "note": "DDS 软件设置的理想映射 A=b+kV，按峰值解释；Vpp=2A"},
        "fit": fit,
        **meta,
    }
    json_path = outdir / "vco_mapping_fit.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return csv_path, json_path


# ---------------------------------------------------------------- CLI 自检 ---

def cli_main(args):
    dg, dsa = find_instruments()
    if args.dg:
        dg = args.dg
    if args.dsa:
        dsa = args.dsa
    if not dg or not dsa:
        raise SystemExit("未找到仪器，用 --dg/--dsa 手动指定 VISA 地址")
    gen = SignalGenerator(dg)
    spec = SpectrumAnalyzer(dsa)
    print("DG :", gen.idn)
    print("DSA:", spec.idn)
    voltages = np.arange(args.start, args.stop + 1e-9, args.step)
    voltages = [round(float(v), 4) for v in voltages]
    try:
        rows = run_sweep(gen, spec, voltages, args.b, args.k, args.settle)
    finally:
        gen.close()
        spec.close()
    if not rows:
        raise SystemExit("没有采到数据")
    fit = fit_rows(rows, args.b, args.k)
    print("拟合:", json.dumps(fit, ensure_ascii=False))
    outdir = Path("outputs") / datetime.now().strftime("%Y%m%d_%H%M%S_cli")
    meta = {"dg": gen.idn, "dsa": spec.idn, "mode": "cli"}
    csv_path, json_path = save_outputs(rows, fit, args.b, args.k, outdir, meta)
    print("已保存:", csv_path, json_path)


# ---------------------------------------------------------------- 图形界面 ---

def gui_main():
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

    root = tk.Tk()
    root.title("VCO 映射关系验证 2026-10-22")
    root.geometry("980x700")

    state = {
        "dg": None, "dsa": None, "rows": [],
        "stop": threading.Event(), "worker": None,
        "queue": queue.Queue(),
    }

    # ---- 仪器面板
    frm_inst = ttk.LabelFrame(root, text="仪器")
    frm_inst.pack(fill="x", padx=8, pady=4)
    ttk.Label(frm_inst, text="信号发生器 (DG)").grid(row=0, column=0, sticky="w")
    ent_dg = ttk.Entry(frm_inst, width=42)
    ent_dg.grid(row=0, column=1, padx=4)
    ttk.Label(frm_inst, text="频谱仪 (DSA815)").grid(row=1, column=0, sticky="w")
    ent_dsa = ttk.Entry(frm_inst, width=42)
    ent_dsa.grid(row=1, column=1, padx=4)
    lbl_inst = ttk.Label(frm_inst, text="未连接", foreground="gray")
    lbl_inst.grid(row=0, column=3, rowSpan=2, padx=8)

    def log(msg):
        state["queue"].put(("log", str(msg)))

    def auto_find():
        dg, dsa = find_instruments(log)
        if dg:
            ent_dg.delete(0, "end"); ent_dg.insert(0, dg)
        if dsa:
            ent_dsa.delete(0, "end"); ent_dsa.insert(0, dsa)

    def connect():
        try:
            if state["dg"]:
                state["dg"].close(); state["dg"] = None
            if state["dsa"]:
                state["dsa"].close(); state["dsa"] = None
            state["dg"] = SignalGenerator(ent_dg.get().strip(), log)
            state["dsa"] = SpectrumAnalyzer(ent_dsa.get().strip(), log)
            lbl_inst.config(text="已连接", foreground="green")
            log("DG : " + state["dg"].idn)
            log("DSA: " + state["dsa"].idn)
        except Exception as e:
            lbl_inst.config(text="连接失败", foreground="red")
            messagebox.showerror("连接失败", str(e))

    ttk.Button(frm_inst, text="自动识别", command=auto_find).grid(row=0, column=2, padx=4)
    ttk.Button(frm_inst, text="连接", command=connect).grid(row=1, column=2, padx=4)

    # ---- 参数面板
    frm_par = ttk.LabelFrame(root, text="扫描与理想映射参数（理想映射 = DDS 软件里的设置）")
    frm_par.pack(fill="x", padx=8, pady=4)
    defaults = {"b": "0.207", "k": "0.0094", "start": "-3.0", "stop": "3.0",
                "step": "0.25", "settle": "0.4"}
    entries = {}
    labels = [("b", "0V 输出振幅 b (V)"), ("k", "调幅斜率 k (V/V)"),
              ("start", "起始电压 (V)"), ("stop", "终止电压 (V)"),
              ("step", "步进 (V)"), ("settle", "稳定时间 (s)")]
    for i, (key, text) in enumerate(labels):
        ttk.Label(frm_par, text=text).grid(row=i // 3, column=(i % 3) * 2, sticky="w", padx=2)
        e = ttk.Entry(frm_par, width=9)
        e.insert(0, defaults[key])
        e.grid(row=i // 3, column=(i % 3) * 2 + 1, padx=2)
        entries[key] = e

    # ---- 绘图
    fig = Figure(figsize=(7.2, 4.4), dpi=100)
    ax = fig.add_subplot(111)
    ax.set_xlabel("VCO 输入电压 / V")
    ax.set_ylabel("RF 输出幅度 Vpp / V (50 Ω)")
    ax.grid(True, alpha=0.3)
    canvas = FigureCanvasTkAgg(fig, master=root)
    canvas.get_tk_widget().pack(fill="both", expand=True, padx=8)

    # ---- 结果与日志
    lbl_fit = ttk.Label(root, text="拟合：尚未扫描", font=("", 10, "bold"))
    lbl_fit.pack(fill="x", padx=10)
    txt = tk.Text(root, height=7)
    txt.pack(fill="x", padx=8, pady=4)

    # ---- 按钮
    frm_btn = ttk.Frame(root)
    frm_btn.pack(fill="x", padx=8, pady=4)

    def redraw():
        ax.clear()
        ax.set_xlabel("VCO 输入电压 / V")
        ax.set_ylabel("RF 输出幅度 Vpp / V (50 Ω)")
        ax.grid(True, alpha=0.3)
        try:
            b = float(entries["b"].get()); k = float(entries["k"].get())
            xs = np.linspace(-HARD_LIMIT_V, HARD_LIMIT_V, 50)
            ax.plot(xs, 2 * (b + k * xs), "c--", label="理想映射 Vpp=2(b+kV)")
        except Exception:
            pass
        rows = state["rows"]
        if rows:
            x = [r["v_set"] for r in rows]
            y = [r["vpp"] for r in rows]
            ax.plot(x, y, "wo", markeredgecolor="k", label="实测")
            fit = fit_rows(rows, float(entries["b"].get()), float(entries["k"].get()))
            if fit:
                xs = np.array(x)
                ax.plot(xs, fit["slope_vpp_per_v"] * xs + fit["intercept_vpp"],
                        "orange", label="实测拟合")
                lbl_fit.config(
                    text=f"拟合: A_vpp = {fit['slope_vpp_per_v']:.4f}·V + {fit['intercept_vpp']:.4f}   "
                         f"R²={fit['r2']:.5f}   斜率偏差 {fit['slope_err_pct']}%   "
                         f"截距偏差 {fit['intercept_err_pct']}%   n={fit['n']}")
        ax.legend(loc="upper left", fontsize=8)
        canvas.draw_idle()

    def save_report():
        rows = state["rows"]
        if not rows:
            messagebox.showinfo("无数据", "先扫描再导出")
            return
        b = float(entries["b"].get()); k = float(entries["k"].get())
        fit = fit_rows(rows, b, k)
        outdir = Path("outputs") / datetime.now().strftime("%Y%m%d_%H%M%S")
        meta = {"dg": state["dg"].idn if state["dg"] else "",
                "dsa": state["dsa"].idn if state["dsa"] else "", "mode": "gui"}
        csv_path, json_path = save_outputs(rows, fit, b, k, outdir, meta)
        fig.savefig(outdir / "vco_mapping_plot.png", dpi=150)
        log(f"已保存到 {outdir}（CSV/JSON/PNG）")
        messagebox.showinfo("已保存", f"{outdir}\n{csv_path.name}\n{json_path.name}\nvco_mapping_plot.png")

    def worker():
        try:
            b = float(entries["b"].get()); k = float(entries["k"].get())
            start = float(entries["start"].get()); stop = float(entries["stop"].get())
            step = float(entries["step"].get()); settle = float(entries["settle"].get())
            if abs(start) > HARD_LIMIT_V or abs(stop) > HARD_LIMIT_V:
                raise ValueError(f"扫描范围不能超过 ±{HARD_LIMIT_V} V（VCO 输入保护）")
            voltages = [round(float(v), 4) for v in np.arange(start, stop + 1e-9, step)]
            state["rows"] = []

            def on_point(row):
                state["rows"].append(row)
                state["queue"].put(("point", row))

            run_sweep(state["dg"], state["dsa"], voltages, b, k, settle,
                      on_point=on_point, should_stop=state["stop"].is_set, log=log)
        except Exception as e:
            state["queue"].put(("log", f"扫描出错: {e}"))
        finally:
            state["queue"].put(("done", None))

    def start_sweep():
        if not state["dg"] or not state["dsa"]:
            messagebox.showinfo("未连接", "先连接仪器")
            return
        if state["worker"] and state["worker"].is_alive():
            return
        state["stop"].clear()
        state["worker"] = threading.Thread(target=worker, daemon=True)
        state["worker"].start()

    def stop_sweep():
        state["stop"].set()
        if state["dg"]:
            state["dg"].output_off()

    ttk.Button(frm_btn, text="开始扫描", command=start_sweep).pack(side="left", padx=4)
    ttk.Button(frm_btn, text="停止", command=stop_sweep).pack(side="left", padx=4)
    ttk.Button(frm_btn, text="导出 CSV/报告", command=save_report).pack(side="left", padx=4)
    ttk.Button(frm_btn, text="清空数据", command=lambda: (state["rows"].clear(), redraw())).pack(side="left", padx=4)

    def poll():
        try:
            while True:
                kind, payload = state["queue"].get_nowait()
                if kind == "log":
                    txt.insert("end", payload + "\n")
                    txt.see("end")
                elif kind == "point":
                    redraw()
                elif kind == "done":
                    redraw()
                    log("扫描结束，DG 输出已关闭")
        except queue.Empty:
            pass
        root.after(150, poll)

    def on_close():
        stop_sweep()
        try:
            if state["dg"]:
                state["dg"].close()
            if state["dsa"]:
                state["dsa"].close()
        finally:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    redraw()
    poll()
    root.mainloop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="VCO 映射关系验证")
    ap.add_argument("--cli", action="store_true", help="无界面自检扫描")
    ap.add_argument("--dg", default="", help="信号发生器 VISA 地址")
    ap.add_argument("--dsa", default="", help="频谱仪 VISA 地址")
    ap.add_argument("--b", type=float, default=0.207, help="理想映射截距 b（峰值 V）")
    ap.add_argument("--k", type=float, default=0.0094, help="理想映射斜率 k（V/V）")
    ap.add_argument("--start", type=float, default=-1.0)
    ap.add_argument("--stop", type=float, default=1.0)
    ap.add_argument("--step", type=float, default=0.5)
    ap.add_argument("--settle", type=float, default=0.4)
    args = ap.parse_args()
    if pyvisa is None:
        raise SystemExit("缺少 pyvisa：pip install -r requirements.txt")
    if args.cli:
        cli_main(args)
    else:
        gui_main()
