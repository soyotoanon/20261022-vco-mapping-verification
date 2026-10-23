"""Discrete measured-cell plot. Missing values never interpolated into colors."""
import numpy as np
from matplotlib.colors import Normalize,LogNorm


def matrices(data):
    shape=(len(data['amplitudes_DAC_codes']),len(data['frequencies_Hz']))
    db=np.full(shape,np.nan);residual=np.full(shape,np.nan)
    for r in data['rows']:
        i,j=r['amplitude_index'],r['frequency_index']
        if r['valid']:
            db[i,j]=r['db']
            if r.get('closed_residual_percent') is not None:
                residual[i,j]=r['closed_residual_percent']
    return db,residual


def draw_map(fig,ax,data,percent=False):
    db,residual=matrices(data);values=residual if percent else db
    ax.clear();ax.set_facecolor('#e2e5e9')
    cmap=__import__('matplotlib').colormaps['viridis' if percent else 'RdYlBu_r'].copy()
    cmap.set_bad('#e2e5e9')
    norm=LogNorm(.001,100) if percent else Normalize(-40,6)
    masked=np.ma.masked_invalid(values)
    if percent:masked=np.ma.masked_less_equal(masked,0)
    im=ax.imshow(masked,origin='lower',interpolation='nearest',aspect='auto',cmap=cmap,norm=norm)
    na,nf=values.shape
    if na>=2 and nf>=2 and np.isfinite(db).any() and np.nanmin(db)<-3<np.nanmax(db):
        ax.contour(np.ma.masked_invalid(db),levels=[-3],colors=['#111111'],linewidths=1.5,corner_mask=False)
    # First reliable upward crossing in each row; no linking across missing rows.
    f=np.asarray(data['frequencies_Hz'])
    if nf>1:
        for i,bw in enumerate(data['bandwidths_Hz']):
            if bw is not None:
                x=np.interp(np.log(bw),np.log(f),np.arange(nf))
                ax.plot(x,i,'D',ms=5,mfc='white',mec='black')
    for r in data['rows']:
        i,j=r['amplitude_index'],r['frequency_index']
        v=values[i,j]
        if not r['valid']:ax.text(j,i,'×',ha='center',va='center',color='#475569')
        elif na*nf<=144 and np.isfinite(v):
            ax.text(j,i,f'{v:.2g}' if percent else f'{v:.1f}',ha='center',va='center',fontsize=7,
                    color='white' if norm(max(v,.001) if percent else v)<.25 else '#111111')
    if data.get('status')=='failed' and data.get('interrupted_cell'):
        c=data['interrupted_cell'];ax.text(c['frequency_index'],c['amplitude_index'],'×',ha='center',va='center')
    stride=max(1,int(np.ceil(nf/16)));ticks=list(range(0,nf,stride))
    ax.set_xticks(ticks,[f'{f[i]:.3g}' for i in ticks],fontsize=8)
    ax.set_yticks(range(na),[str(a) for a in data['amplitudes_DAC_codes']],fontsize=8)
    ax.set_xticks(np.arange(-.5,nf,1),minor=True);ax.set_yticks(np.arange(-.5,na,1),minor=True)
    ax.grid(which='minor',color='white',linewidth=.5);ax.tick_params(which='minor',length=0)
    ax.set_xlabel('频率 / Hz（离散对数频点）');ax.set_ylabel('扰动峰值 / DAC码')
    label='闭环残余基频峰值 / %' if percent else '闭环/开环振幅 / dB'
    ax.set_title('④ 频率 × 幅度 · 黑线：−3 dB等值线；◇：该行首个可靠上穿点',loc='left',fontsize=10)
    if percent and not np.isfinite(values).any():
        ax.text(.5,.5,'尚无可用百分比：先完成可靠的暗场/噪声测量',transform=ax.transAxes,ha='center')
    cb=fig.colorbar(im,ax=ax,pad=.015,extend='both');cb.set_label(label,fontsize=9)
    return cb
