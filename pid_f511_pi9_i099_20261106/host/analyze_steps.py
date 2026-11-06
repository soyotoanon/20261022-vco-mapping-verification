"""Plot captured target steps, using ADC observations rather than UART timing claims."""
import argparse
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def main():
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);args=p.parse_args()
    fig,axes=plt.subplots(2,2,figsize=(10,7),layout='constrained');records=[]
    for index,ax in enumerate(axes.flat):
        meta=json.loads((args.folder/f'{index:02d}_step.json').read_text())
        y=np.load(args.folder/f'{index:02d}_step.npz')['raw'].astype(float)/4
        start=float(np.median(y[:32]));end=float(np.median(y[-2048:]));span=end-start
        if abs(span)<10:raise RuntimeError('Step amplitude is too small for this analysis.')
        progress=(y-start)/span
        ten=np.flatnonzero(progress>=.1)
        if not len(ten):raise RuntimeError('No step edge found.')
        t10=int(ten[0]);ninety=np.flatnonzero(progress[t10:]>=.9)
        if not len(ninety):raise RuntimeError('Step never reached 90%.')
        t90=t10+int(ninety[0]);dt=1/meta['fs_hz']
        time_us=(np.arange(len(y))-t10)*dt*1e6
        use=(time_us>=-100)&(time_us<=400)
        ax.plot(time_us[use],y[use],'.-',markersize=3)
        event=meta['host_event']
        ax.axhline(event['new_target_adc'],color='gray',linestyle='--',linewidth=.8)
        ax.set(title=f"Target {event['previous_target_adc']} to {event['new_target_adc']} ADC codes",
               xlabel='Time relative to observed 10% crossing (us)',ylabel='In-loop ADC code')
        ax.grid(alpha=.2)
        records.append(dict(step=index,observed_10_to_90_us=(t90-t10)*dt*1e6,
                            sample_spacing_us=dt*1e6,initial_adc=start,final_adc=end,
                            qualification='CIC-filtered in-loop sample estimate; not absolute UART-to-optical latency'))
    fig.suptitle('Runtime setpoint changes: captured ADC response, 6.4 us sample spacing')
    fig.savefig(args.folder/'setpoint_steps.png',dpi=170);plt.close(fig)
    (args.folder/'step_observations.json').write_text(json.dumps(records,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(records,indent=2))

if __name__=='__main__':main()
