"""Summarize repeated in-loop noise captures without claiming statistical significance."""
import argparse
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def main():
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path);args=parser.parse_args()
    restore=json.loads((args.folder/'restoration.json').read_text())
    if not restore['restored'] or restore.get('trial_error'):raise RuntimeError('Noise measurement incomplete.')
    rows=json.loads((args.folder/'summary.json').read_text())
    grouped={}
    for r in rows:
        if r['state']!='closed':continue
        key=f"P={r['kp']:g}, I={r['ki']:.5f}"
        entry=grouped.setdefault(key,{})
        for metric,value in r['bands'].items():entry.setdefault(metric,[]).append(value)
    result={key:{metric:dict(values=values,mean=float(np.mean(values)),minimum=min(values),maximum=max(values))
                 for metric,values in group.items()} for key,group in grouped.items()}
    result['interpretation']='Two repetitions per profile; overlap/variation does not establish significant improvement. ADC-derived in-loop noise, not independent optical RIN.'
    (args.folder/'repeat_comparison.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    fig,axes=plt.subplots(1,2,figsize=(12,5),layout='constrained')
    colors={9:'#386d9e',11:'#c4612a'};plotted=set()
    for row in rows:
        if row['state']!='closed':continue
        axis=axes[0 if row['decimation_log2']==8 else 1]
        path=args.folder/f"{row['trial']:02d}_closed_d{row['decimation_log2']}.npz"
        record=np.load(path);f=record['frequency_hz'];psd=record['rin_psd_per_hz']
        ix=(f>=10)&(f<=1000) if row['decimation_log2']==8 else (f>=1000)&(f<=120000)
        key=(row['kp'],row['decimation_log2'])
        label=f"P={row['kp']:g}, I={row['ki']:.5f}" if key not in plotted else None
        plotted.add(key);axis.loglog(f[ix],psd[ix],color=colors[row['kp']],alpha=.65,label=label,linewidth=.8)
    for axis,title in zip(axes,['10 Hz – 1 kHz','1 kHz – 120 kHz']):
        axis.set(title=title,xlabel='Frequency (Hz)',ylabel='In-loop relative power PSD (1/Hz)')
        axis.grid(which='both',alpha=.2);axis.legend(fontsize=9)
    fig.suptitle('Alternating original/candidate/original/candidate captures; individual repetitions shown')
    fig.savefig(args.folder/'noise_repeats.png',dpi=170);plt.close(fig)
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
