"""Summarize completed, restored scans without opening any instruments."""
from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

root=Path(__file__).resolve().parents[1]
rows=[]
for folder in sorted((root/'results').glob('*_pi_scan')):
    if not (folder/'restoration.json').exists():continue
    restored=json.loads((folder/'restoration.json').read_text())
    if not restored['restored'] or restored.get('trial_error'):continue
    initial=json.loads((folder/'initial_registers.json').read_text())
    for trial in json.loads((folder/'results.json').read_text()):
        points=trial['points']
        if not points:continue
        worst=max(points,key=lambda x:x['closed_over_held_db'])
        row=dict(kp=trial['kp'],ki=trial['ki'],target_adc=initial['18'],
                 source=folder.relative_to(root).as_posix(),points=points,
                 maximum_tested_amplification_db=worst['closed_over_held_db'],
                 maximum_tested_amplification_frequency_hz=worst['requested_hz'])
        rows.append(row)
(root/'results/scan_summary.json').write_text(json.dumps(rows,indent=2)+'\n',encoding='utf-8')
fig,ax=plt.subplots(figsize=(10,6),layout='constrained')
selected=[r for r in rows if r['kp'] in (2,4,8,10,11,12,18)]
for row in selected:
    p=sorted(row['points'],key=lambda x:x['requested_hz'])
    ax.semilogx([x['requested_hz'] for x in p],[x['closed_over_held_db'] for x in p],
                'o-',markersize=4,label=f"P={row['kp']:g}, I={row['ki']:.2f}")
ax.axhline(0,color='gray',linewidth=.8)
ax.axhline(-3,color='gray',linestyle='--',linewidth=.8)
ax.set(xlabel='Injected disturbance frequency (Hz)',ylabel='Closed / held tone amplitude (dB)',
       title='F511 at approximately 1 mW: measured in-loop disturbance rejection')
ax.grid(which='both',alpha=.2);ax.legend(ncols=2,fontsize=8)
fig.savefig(root/'results/scan_comparison_1mw.png',dpi=170);plt.close(fig)
print('COMPLETED_TRIALS',len(rows))
for row in rows:
    print('SUMMARY',json.dumps({k:v for k,v in row.items() if k not in ('points','source')}))
