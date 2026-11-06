"""Read a completed sweep and plot measured rejection; no instrument access."""
import argparse
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def analyze(folder):
    restoration=json.loads((folder/'restoration.json').read_text())
    if not restoration['restored'] or restoration.get('trial_error'):
        raise RuntimeError('Sweep did not finish and restore correctly.')
    rows=json.loads((folder/'results.json').read_text())
    if len(rows)!=1:raise RuntimeError('Select a single-profile qualification sweep.')
    trial=rows[0];points=sorted(trial['points'],key=lambda p:p['requested_hz'])
    f=np.array([p['requested_hz'] for p in points]);db=np.array([p['closed_over_held_db'] for p in points])
    crossings=[]
    for i in range(len(points)-1):
        if db[i]<=-3<db[i+1]:
            logf=np.log10(f[i])+(-3-db[i])/(db[i+1]-db[i])*(np.log10(f[i+1])-np.log10(f[i]))
            crossings.append(dict(estimated_hz=float(10**logf),bracket_hz=[float(f[i]),float(f[i+1])]))
    peak=int(np.argmax(db))
    result=dict(source=str(folder.resolve()),kp=trial['kp'],ki=trial['ki'],
                minus_3db_upward_crossings=crossings,
                maximum_measured_amplification_db=float(db[peak]),peak_frequency_hz=float(f[peak]),
                measured_frequency_range_hz=[float(f.min()),float(f.max())],measured_points=len(points),
                all_held_tones_resolved=all(p['held_tone_resolved'] for p in points),
                peak_below_7db_on_measured_grid=bool(db[peak]<=7),
                measurement='20 log10(closed fitted tone amplitude / held fitted tone amplitude); in-loop ADC, actuator injection',
                bandwidth_method='First upward -3 dB crossing, interpolated in log frequency between adjacent measured points',
                limitations='Finite measured frequency grid, one operating point, not independent out-of-loop optical RIN')
    (folder/'qualification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    fig,ax=plt.subplots(figsize=(10,5.7),layout='constrained')
    ax.semilogx(f,db,'o-',label=f"P={trial['kp']:g}, I={trial['ki']:.5f}",color='#176c9e',markersize=4)
    ax.axhline(7,color='#bc3a35',linestyle='--',label='+7 dB limit')
    ax.axhline(-3,color='#49814f',linestyle='--',label='-3 dB')
    ax.axhline(0,color='gray',linewidth=.7)
    ax.axvline(15000,color='#49814f',linestyle=':',label='15 kHz target')
    ax.set(xlabel='Disturbance frequency (Hz)',ylabel='Closed / held tone amplitude (dB)',
           title='Measured in-loop disturbance rejection at approximately 1 mW')
    ax.grid(which='both',alpha=.2);ax.legend(fontsize=9,ncols=2)
    fig.savefig(folder/'qualification.png',dpi=180);plt.close(fig)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('folder',type=Path)
    args=parser.parse_args();print(json.dumps(analyze(args.folder),indent=2))
