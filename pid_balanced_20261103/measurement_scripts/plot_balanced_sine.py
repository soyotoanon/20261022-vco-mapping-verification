from pathlib import Path
import json,os,hashlib
import numpy as np
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/balanced_followup'
os.environ['MPLCONFIGDIR']=str(OUT/'mpl-cache')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','DejaVu Sans'],'axes.unicode_minus':False,'font.size':11})
source=ROOT/'outputs/pi_three_20261103/three_tones_v2_results.json'
rows=json.loads(source.read_text(encoding='utf-8'))
f=np.array([r['frequency_hz'] for r in rows],float)
g=np.array([-next(c for c in r['captures'] if c['name']=='balanced')['suppression_db'] for r in rows])
assert len(rows)==14 and all(r['amplitude_dac_peak']==64 for r in rows)
def cross(level):
    i=next(i for i in range(len(f)-1) if g[i]<level<=g[i+1])
    return float(np.exp(np.interp(level,g[i:i+2],np.log(f[i:i+2])))),[float(f[i]),float(f[i+1])]
bw,bracket=cross(-3.0103);zero,zbracket=cross(0);j=int(np.argmax(g))
fig,(ax,zoom)=plt.subplots(1,2,figsize=(14,6.6),gridspec_kw={'width_ratios':[1.55,1]})
fig.subplots_adjust(top=.80,bottom=.17,left=.07,right=.98,wspace=.23)
for a in [ax,zoom]:
    a.semilogx(f/1000,g,'o-',color='#216b62',lw=2,ms=5,label='实测频点（折中组）')
    a.axhline(0,color='#555',lw=1.1);a.axhline(-3.0103,color='#b97827',ls='--',lw=1.3)
    a.axvline(bw/1000,color='#216b62',ls='--',lw=1.2);a.axvline(zero/1000,color='#777',ls=':',lw=1.2)
    a.axvspan(1,bw/1000,color='#216b62',alpha=.055)
    a.grid(which='both',alpha=.18);a.set_xlabel('正弦频率 / kHz')
ax.set(xlim=(.9,110),ylim=(-30,12),ylabel='增益 = 20 log10(闭环振幅 / 暂停反馈振幅) / dB')
ax.set_xticks([1,3,10,30,100],labels=['1','3','10','30','100'])
ax.text(1.15,-26,'负值：抑制扰动',color='#216b62',fontsize=12)
ax.text(1.15,8.5,'正值：放大扰动',color='#a35e37',fontsize=12)
ax.annotate(f'已测放大峰 +{g[j]:.2f} dB\n{f[j]/1000:g} kHz',xy=(f[j]/1000,g[j]),xytext=(18,10),ha='center',va='top',arrowprops={'arrowstyle':'->','color':'#a35e37'},color='#914e2d')
ax.legend(loc='lower left',bbox_to_anchor=(.36,.02),frameon=False)
zoom.set(xlim=(8,30),ylim=(-12,5),title='带宽附近放大查看')
zoom.set_xticks([8,10,12,14,16,20,25,30],labels=['8','10','12','14','16','20','25','30'])
zoom.plot([bw/1000],[-3.0103],'o',color='#b97827',ms=8)
zoom.annotate(f'−3 dB带宽\n{bw/1000:.2f} kHz',xy=(bw/1000,-3.0103),xytext=(9.2,-.7),arrowprops={'arrowstyle':'->','color':'#b97827'},color='#98601b',fontsize=12)
zoom.plot([zero/1000],[0],'o',color='#555',ms=7)
zoom.annotate(f'0 dB交点\n{zero/1000:.2f} kHz',xy=(zero/1000,0),xytext=(19,-8),arrowprops={'arrowstyle':'->','color':'#555'},fontsize=12)
fig.suptitle('折中组 P=11，I=0.72000122：主动注入正弦的抑制曲线',fontsize=17,y=.97)
fig.text(.5,.89,'注入位置：控制DAC输出端｜64 DAC码峰值 / 128码峰峰值｜PD1 / ADC同频拟合',ha='center',fontsize=12)
fig.text(.5,.045,'采用2026-11-03已采集的14个频点；阈值按对数频率插值。14–16 kHz附近光学扰动峰值约0.020 mW（历史标定）。\n这张图是主动正弦试验，不是自然噪声谱；本次重绘不代表重新扫频。',ha='center',fontsize=10,color='#555')
for ext in ['png','svg']:fig.savefig(OUT/f'折中组_主动正弦抑制曲线.{ext}',dpi=180)
plt.close(fig)
result=dict(kp=11,ki=.720001220703125,injection_dac_peak=64,injection_dac_peak_to_peak=128,bandwidth_minus3db_hz=bw,bandwidth_bracket_hz=bracket,zero_db_hz=zero,zero_db_bracket_hz=zbracket,peak_gain_db=float(g[j]),peak_frequency_hz=float(f[j]),source=str(source),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),frequency_hz=f.tolist(),gain_db=g.tolist(),suppression_db=(-g).tolist())
(OUT/'balanced_sine_curve.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:result[k] for k in ['bandwidth_minus3db_hz','zero_db_hz','peak_gain_db','peak_frequency_hz']}))
