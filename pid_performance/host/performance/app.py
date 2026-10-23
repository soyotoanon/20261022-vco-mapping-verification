from __future__ import annotations
import os,sys,queue,threading,traceback
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import ttk,messagebox
import numpy as np
import matplotlib
matplotlib.use('Agg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from PIL import Image,ImageTk

BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE.parent/'vendor'))
from device import Device
from measurement import Measurement

matplotlib.rcParams['font.sans-serif']=['Microsoft YaHei','SimHei','DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus']=False


class PlotCanvas:
    """Explicit bitmap sizing avoids Windows mixed-DPI TkAgg resize artifacts."""
    def __init__(self,figure,master):
        self.figure=figure;self.renderer=FigureCanvasAgg(figure)
        self.widget=tk.Canvas(master,highlightthickness=0,background='#f5f7fa')
        self.item=self.widget.create_image(0,0,anchor='nw');self.pending=None
        self.widget.bind('<Configure>',lambda e:self.draw_idle())
    def get_tk_widget(self):return self.widget
    def draw_idle(self):
        if self.pending:self.widget.after_cancel(self.pending)
        self.pending=self.widget.after(80,self.draw)
    def draw(self):
        self.pending=None
        w,h=self.widget.winfo_width(),self.widget.winfo_height()
        if w<100 or h<100:return
        self.figure.set_size_inches(w/100,h/100);self.figure.set_dpi(100)
        self.renderer.draw()
        self.photo=ImageTk.PhotoImage(Image.fromarray(np.asarray(self.renderer.buffer_rgba()).copy()))
        self.widget.itemconfigure(self.item,image=self.photo)


class App:
    def __init__(self,root):
        self.root=root;root.title('光强反馈 · 性能测量');root.geometry('1120x820');root.minsize(960,720)
        self.events=queue.Queue();self.cancel=threading.Event();self.busy=False
        self.dev=None;self.measure=None;self.spectra=None;self.folder=None;self.closing=False
        style=ttk.Style();style.theme_use('clam')
        style.configure('TButton',padding=(10,7));style.configure('TFrame',background='#f5f7fa')
        style.configure('TLabel',background='#f5f7fa');root.configure(background='#f5f7fa')
        top=ttk.Frame(root,padding=(20,16));top.pack(fill='x')
        ttk.Label(top,text='光强反馈 · 性能测量',font=('Microsoft YaHei',19,'bold')).pack(anchor='w')
        ttk.Label(top,text='PD → ADC   ·   环内测量   ·   原始数据自动保存',foreground='#526174').pack(anchor='w',pady=(4,12))
        row=ttk.Frame(top);row.pack(fill='x')
        self.port=tk.StringVar(value='COM9');self.low=tk.StringVar(value='0.5');self.high=tk.StringVar(value='100000');self.amp=tk.StringVar(value='128')
        for label,var,width in [('串口',self.port,7),('频段 / Hz',self.low,9),('至',self.high,9),('扰动 / DAC码',self.amp,6)]:
            ttk.Label(row,text=label).pack(side='left',padx=(0,5))
            ttk.Entry(row,textvariable=var,width=width).pack(side='left',padx=(0,12))
        self.connect=ttk.Button(row,text='连接',command=self.connect_device);self.connect.pack(side='left')
        actions=ttk.Frame(top);actions.pack(fill='x',pady=(13,0))
        self.buttons=[]
        for label,cmd in [('① 测噪声谱',self.noise),('② 计算 RMS',self.rms),('③ 测抑制带宽',self.sweep)]:
            b=ttk.Button(actions,text=label,command=cmd,state='disabled');b.pack(side='left',padx=(0,8));self.buttons.append(b)
        self.stop=ttk.Button(actions,text='停止',command=self.cancel.set,state='disabled');self.stop.pack(side='left')
        ttk.Button(actions,text='保存图 / 查看数据',command=self.save).pack(side='right')
        self.status=tk.StringVar(value='连接后按 ① → ② → ③ 测量；先确保原 PID 已稳定闭环。')
        ttk.Label(top,textvariable=self.status,wraplength=1050,foreground='#345a80').pack(anchor='w',pady=(12,0))
        self.result=tk.StringVar(value='RMS：—     |     3 dB 噪声抑制带宽：—')
        ttk.Label(top,textvariable=self.result,font=('Microsoft YaHei',12,'bold')).pack(anchor='w',pady=(9,0))
        self.fig=Figure(figsize=(10.5,5.2),dpi=100,layout='constrained',facecolor='#f5f7fa')
        self.a,self.b=self.fig.subplots(2,1)
        self.canvas=PlotCanvas(self.fig,master=root);self.canvas.get_tk_widget().configure(height=450)
        self.canvas.get_tk_widget().pack(fill='both',expand=True,padx=14)
        self.reset_axes()
        ttk.Label(root,text='噪声底＝遮光后的 PD＋ADC 噪声；不包含有光散粒噪声。环内结果不能替代独立 PD 的实际稳光验证。',
                  padding=(20,10),foreground='#607080').pack(fill='x')
        self.rms_text='RMS：—';self.bw_text='3 dB 噪声抑制带宽：—'
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(100,self.poll)

    def reset_axes(self):
        for ax in (self.a,self.b):ax.grid(alpha=.2);ax.set_xscale('log');ax.set_xlabel('频率 / Hz')
        self.a.set_ylabel('RIN / dBc/Hz');self.a.set_title('① 相对强度噪声谱',loc='left',fontsize=11)
        self.b.set_ylabel('闭环 / 开环振幅 · dB');self.b.set_title('③ 噪声抑制曲线',loc='left',fontsize=11)
        self.b.axhline(-3,color='#7c8798',ls='--',lw=1);self.b.axhline(0,color='#bbc3cf',lw=.8)
        self.canvas.draw_idle()

    def notify(self,kind,data):self.events.put((kind,data))

    def prompt(self,title,text):
        ready=threading.Event();answer=[]
        self.events.put(('prompt',(title,text,ready,answer)))
        while not ready.wait(.1):
            if self.cancel.is_set():return False
        return bool(answer and answer[0])

    def worker(self,func):
        if self.busy:return
        self.busy=True;self.cancel.clear();self.update_buttons()
        def run():
            try:func()
            except Exception as e:
                if self.folder:
                    with (self.folder/'events.log').open('a',encoding='utf-8') as f:f.write(traceback.format_exc()+'\n')
                self.notify('error',str(e) or '已停止')
            finally:
                if self.dev:
                    try:self.dev.inject(0,0);self.dev.hold(False)
                    except Exception as e:self.notify('error','恢复控制状态失败：'+str(e))
                self.notify('done',None)
        threading.Thread(target=run,daemon=True).start()

    def update_buttons(self):
        self.connect.configure(state='disabled' if self.busy or self.dev else 'normal')
        for i,b in enumerate(self.buttons):
            available=self.dev is not None and (i!=1 or self.spectra is not None)
            b.configure(state='normal' if available and not self.busy else 'disabled')
        self.stop.configure(state='normal' if self.busy else 'disabled')

    def settings(self):
        low,high=float(self.low.get()),float(self.high.get())
        if not 0<low<high:raise ValueError('频率下限必须小于上限且为正')
        return low,high

    def connect_device(self):
        port=self.port.get().strip()
        def work():
            self.dev=Device(port)
            self.folder=BASE.parents[1]/'measurements'/datetime.now().strftime('%Y%m%d_%H%M%S')
            self.measure=Measurement(self.dev,self.folder,self.cancel,self.notify,self.prompt)
            self.notify('status',f'已连接 {port} · ADC {self.dev.raw_fs/1e6:g} MS/s · A623 固件')
        self.worker(work)

    def noise(self):
        try:low,high=self.settings()
        except Exception as e:messagebox.showerror('频段',str(e));return
        def work():
            self.spectra=self.measure.noise(low,high)
            self.notify('spectrum',self.spectra)
            self.notify('status','噪声谱已保存；可以计算所选频段的 RMS。')
        self.worker(work)

    def rms(self):
        try:
            low,high=self.settings();r=self.measure.rms(self.spectra,low,high)
            self.rms_text=f"RMS ({low:g}–{high:g} Hz)：开环 {100*r['open']:.4g}% → 闭环 {100*r['closed']:.4g}%"
            self.result.set(self.rms_text+'     |     '+self.bw_text)
            self.status.set('RMS 已保存；由单边 RIN 功率谱积分计算，未扣除噪声底。')
        except Exception as e:messagebox.showerror('RMS',str(e))

    def sweep(self):
        try:low,high=self.settings();amp=int(self.amp.get())
        except Exception as e:messagebox.showerror('扫频设置',str(e));return
        def work():
            self.measure.sweep(low,high,amp)
            self.notify('status','10 个对数间隔频点测量完成，原始数据和抑制曲线已保存。')
        self.worker(work)

    def draw_spectrum(self,data):
        self.a.clear();self.a.set_xscale('log');self.a.grid(alpha=.2)
        for key,label,color in [('open','开环','#94a3b8'),('closed','闭环','#176bc5'),('floor','暗场噪声底','#c28a3c')]:
            self.a.plot(data['f'],10*np.log10(np.maximum(data[key],1e-30)),label=label,color=color,lw=1)
        self.a.set_ylabel('RIN / dBc/Hz');self.a.set_xlabel('频率 / Hz');self.a.legend(loc='best',fontsize=9)
        self.a.set_title('① 相对强度噪声谱 · 环内',loc='left',fontsize=11);self.canvas.draw_idle()

    def draw_sweep(self,data):
        rows=data['rows'];f=[r['frequency_Hz'] for r in rows];db=[r['db'] if r['valid'] else np.nan for r in rows]
        self.b.clear();self.b.set_xscale('log');self.b.grid(alpha=.2)
        self.b.axhline(-3,color='#7c8798',ls='--',label='−3 dB');self.b.axhline(0,color='#bbc3cf',lw=.8)
        self.b.plot(f,db,'o-',color='#176bc5');self.b.set_ylabel('闭环 / 开环振幅 · dB');self.b.set_xlabel('频率 / Hz')
        self.b.set_title('③ 噪声抑制曲线',loc='left',fontsize=11)
        bw=data['bandwidth_Hz']
        self.bw_text=f'3 dB 抑制带宽：{bw:g} Hz（插值）' if bw else '3 dB 抑制带宽：尚无可靠交点'
        self.result.set(self.rms_text+'     |     '+self.bw_text);self.canvas.draw_idle()

    def poll(self):
        try:
            while True:
                kind,data=self.events.get_nowait()
                if kind=='status':self.status.set(data)
                elif kind=='error':self.status.set('未完成：'+data)
                elif kind=='spectrum':self.draw_spectrum(data)
                elif kind=='sweep':self.draw_sweep(data)
                elif kind=='done':
                    self.busy=False;self.update_buttons()
                    if self.closing:self.close();return
                elif kind=='prompt':
                    title,text,event,answer=data
                    answer.append(False if self.cancel.is_set() else messagebox.askokcancel(title,text,parent=self.root))
                    event.set()
        except queue.Empty:pass
        self.root.after(100,self.poll)

    def save(self):
        if not self.folder:self.status.set('尚无测量数据。');return
        self.fig.savefig(self.folder/'results.png',dpi=180)
        self.fig.savefig(self.folder/'results.pdf')
        os.startfile(self.folder)

    def close(self):
        if self.busy:
            self.closing=True;self.cancel.set();self.status.set('正在停止并恢复控制状态……');return
        if self.dev:
            try:self.dev.close()
            except Exception as e:messagebox.showerror('连接状态',str(e))
        self.root.destroy()


if __name__=='__main__':
    import ctypes
    try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:pass
    root=tk.Tk();root.tk.call('tk','scaling',1.333333);root.geometry('+0+0');app=App(root)
    if '--preview' in sys.argv:
        app.status.set('界面预览 · 未连接硬件 · 无测量数据')
        if '--screenshot' in sys.argv:
            root.lift();root.attributes('-topmost',True)
            def shot():
                from PIL import ImageGrab
                root.update();x,y=root.winfo_rootx(),root.winfo_rooty()
                ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height())).save(BASE.parents[1]/'ui_preview.png')
                root.destroy()
            root.after(1500,shot)
    root.mainloop()
