"""Summarize a completed broad PI scan; finite measured candidates, no global claim."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def main():
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path);args=parser.parse_args()
    restore=json.loads((args.folder/'restoration.json').read_text())
    if not restore['restored'] or restore.get('trial_error'):raise RuntimeError('Scan incomplete or failed.')
    trials=json.loads((args.folder/'results.json').read_text());summary=[]
    fig,axes=plt.subplots(1,2,figsize=(12,5),layout='constrained');seen={}
    for trial in trials:
        points=trial['points'];p15=next(p for p in points if p['requested_hz']==15000)
        worst=max(points,key=lambda p:p['closed_over_held_db'])
        summary.append(dict(index=trial['index'],kp=trial['kp'],ki=trial['ki'],
            db_at_15khz=p15['closed_over_held_db'],maximum_tested_db=worst['closed_over_held_db'],
            maximum_tested_frequency_hz=worst['requested_hz'],all_held_resolved=all(p['held_tone_resolved'] for p in points)))
        key=(trial['kp'],trial['ki']);offset=6+12*seen.get(key,0);seen[key]=seen.get(key,0)+1
        for axis in axes:axis.scatter(trial['kp'],trial['ki'],s=30,color='#174d7a')
        axes[0].annotate(f"{p15['closed_over_held_db']:.1f}",(trial['kp'],trial['ki']),xytext=(5,offset),textcoords='offset points',fontsize=9)
        axes[1].annotate(f"{worst['closed_over_held_db']:.1f}",(trial['kp'],trial['ki']),xytext=(5,offset),textcoords='offset points',fontsize=9,
                         color='#ae2929' if worst['closed_over_held_db']>7 else '#206135')
    for axis,title in zip(axes,['Suppression at 15 kHz (dB, more negative is stronger)',
                               'Largest amplification on measured grid (dB, limit +7)']):
        axis.set(xlabel='P',ylabel='I per valid sample',title=title,
                 xlim=(min(t['kp'] for t in trials)-1,max(t['kp'] for t in trials)+3),
                 ylim=(min(t['ki'] for t in trials)-.06,1.08));axis.grid(alpha=.2)
    fig.suptitle('Broader measured PI search at approximately 1 mW; labels are measurements, not interpolation')
    fig.savefig(args.folder/'parameter_search.png',dpi=170);plt.close(fig)
    (args.folder/'comparison.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    for row in summary:print(json.dumps(row))

if __name__=='__main__':main()
