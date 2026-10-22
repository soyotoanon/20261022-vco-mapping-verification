# -*- coding: utf-8 -*-
"""VCO 映射数据查看器：实测 vs DDS 设置的理想映射。

用法:  python plot_viewer.py
左侧选择 outputs/ 下的数据集，右侧显示:
  上图: 实测点 + 线性拟合 + 理想映射直线
  下图: 残差(实测 - 理想)
"""
import csv
import json
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import matplotlib

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import matplotlib.font_manager as fm

# 中文字体
for name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC"):
    if any(f.name == name for f in fm.fontManager.ttflist):
        matplotlib.rcParams["font.sans-serif"] = [name]
        break
matplotlib.rcParams["axes.unicode_minus"] = False

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"

IDEAL_B_PP = 0.414   # 理想截距 Vpp = 2*0.207
IDEAL_K_PP = 0.0188  # 理想斜率 Vpp/V = 2*0.0094


def load_dataset(d: Path):
    csvs = list(d.glob("*.csv"))
    if not csvs:
        return None
    rows = []
    with open(csvs[0], newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append({k: float(v) for k, v in r.items()})
    fit = None
    js = list(d.glob("*fit*.json"))
    if js:
        fit = json.loads(js[0].read_text(encoding="utf-8"))
    return rows, fit


def linfit(xs, ys):
    n = len(xs)
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    k = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    b = (sy - k * sx) / n
    return k, b


class Viewer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("VCO 映射关系验证 - 实测 vs 设置")
        self.geometry("1100x640")

        left = ttk.Frame(self, width=240)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=6)
        ttk.Label(left, text="数据集 (outputs/)", font=("", 10, "bold")).pack(anchor=tk.W)
        self.lb = tk.Listbox(left, width=34, height=24)
        self.lb.pack(fill=tk.Y, expand=True)
        self.lb.bind("<<ListboxSelect>>", self.on_select)
        ttk.Button(left, text="刷新列表", command=self.refresh).pack(fill=tk.X, pady=4)
        self.info = tk.Text(left, width=34, height=12, wrap=tk.WORD)
        self.info.pack(fill=tk.X)

        self.fig = Figure(figsize=(8.6, 6.2), dpi=100)
        self.ax1 = self.fig.add_subplot(211)
        self.ax2 = self.fig.add_subplot(212, sharex=self.ax1)
        self.fig.tight_layout(h_pad=2.5)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.refresh()
        if self.lb.size():
            self.lb.selection_set(self.lb.size() - 1)  # 默认最新
            self.on_select()

    def refresh(self):
        self.lb.delete(0, tk.END)
        if OUT.is_dir():
            for d in sorted(OUT.iterdir()):
                if d.is_dir() and list(d.glob("*.csv")):
                    self.lb.insert(tk.END, d.name)

    def on_select(self, *_):
        sel = self.lb.curselection()
        if not sel:
            return
        d = OUT / self.lb.get(sel[0])
        got = load_dataset(d)
        if not got:
            return
        rows, fitj = got
        xs = [r["v_set"] for r in rows]
        ys = [r["vpp"] for r in rows]
        k, b = linfit(xs, ys)
        x0, x1 = min(xs), max(xs)
        ideal = [IDEAL_B_PP + IDEAL_K_PP * x for x in (x0, x1)]
        fitl = [b + k * x for x in (x0, x1)]

        self.ax1.clear()
        self.ax2.clear()
        self.ax1.plot((x0, x1), [v * 1000 for v in ideal], "g--", lw=2,
                      label=f"设置的理想映射  A={IDEAL_B_PP:.3f}+{IDEAL_K_PP:.4f}·V")
        self.ax1.plot(xs, [y * 1000 for y in ys], "ro", ms=6, label="实测点")
        self.ax1.plot((x0, x1), [v * 1000 for v in fitl], "b-", lw=1.5,
                      label=f"实测拟合  A={b:.3f}+{k:.4f}·V")
        self.ax1.set_ylabel("输出幅度 (mVpp)")
        self.ax1.set_title(f"{d.name}   实测/设置 ≈ {b / IDEAL_B_PP:.3f} (截距比), {k / IDEAL_K_PP:.3f} (斜率比)")
        self.ax1.grid(alpha=0.3)
        self.ax1.legend(loc="upper left", fontsize=9)

        res = [(y - (IDEAL_B_PP + IDEAL_K_PP * x)) * 1000 for x, y in zip(xs, ys)]
        self.ax2.axhline(0, color="g", ls="--", lw=1)
        self.ax2.plot(xs, res, "ms-", lw=1)
        self.ax2.set_xlabel("AM 输入电压 (V)")
        self.ax2.set_ylabel("实测-理想 (mVpp)")
        self.ax2.grid(alpha=0.3)
        self.canvas.draw_idle()

        self.info.delete("1.0", tk.END)
        ratio_b, ratio_k = b / IDEAL_B_PP, k / IDEAL_K_PP
        self.info.insert(tk.END,
            f"数据集: {d.name}\n"
            f"点数: {len(rows)}  范围: {x0:+.1f}~{x1:+.1f} V\n"
            f"实测: A = {b:.4f} + {k:.5f}·V (Vpp)\n"
            f"理想: A = {IDEAL_B_PP:.4f} + {IDEAL_K_PP:.5f}·V (Vpp)\n"
            f"截距比: {ratio_b:.3f}   斜率比: {ratio_k:.3f}\n")
        if fitj and "fit" in fitj:
            f = fitj["fit"]
            self.info.insert(tk.END, f"R² = {f.get('r2', 0):.5f}\n")
        # 400mVpp 所需电压
        need = (0.400 - b) / k
        self.info.insert(tk.END,
            f"\n按实测曲线, 400 mVpp 需要\n输入 {need:+.2f} V"
            + ("（超出±5V!）" if abs(need) > 5 else ""))


if __name__ == "__main__":
    Viewer().mainloop()
