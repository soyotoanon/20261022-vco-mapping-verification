import csv,json,time
from dataclasses import asdict
from pathlib import Path
import numpy as np
from analysis import spectrum,relative_density,band_rms,tone,rejection,crossing


def save_json(path,obj):
    def convert(x):
        if isinstance(x,np.ndarray):return x.tolist()
        if isinstance(x,np.generic):return x.item()
        raise TypeError(type(x).__name__)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=convert,allow_nan=False),encoding='utf-8')


def rates(raw_fs,low,high):
    if not 0<low<high<=raw_fs/10:raise ValueError('频段必须为正，最高频率不超过 ADC 采样率的 1/10')
    result=[]
    for log in [14,12,8,4,0]:
        fs=raw_fs/2**log
        if fs/10>=low and 3*fs/2048<=high:result.append(log)
    if not result or min(3*raw_fs/2**d/2048 for d in result)>low:
        raise ValueError('当前缓存长度无法覆盖所选最低频率')
    return result


class Measurement:
    def __init__(self,dev,folder,cancel,notify,prompt):
        self.dev=dev;self.folder=Path(folder);self.cancel=cancel
        self.notify=notify;self.prompt=prompt;self.counter=0
        self.folder.mkdir(parents=True,exist_ok=True)
        save_json(self.folder/'session.json',dict(registers=dev.snapshot,raw_sample_rate_Hz=dev.raw_fs,
            detector='in-loop feedback ADC',created=time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            noise_floor='dark PD + ADC; excludes illuminated shot noise',
            spectral_method='one-sided Welch, Hann, 2048 samples, 50% overlap, per-segment DC removal',
            decimator='CIC3; passband correction; retained frequencies <= output_fs/10',
            limitation='Analog anti-alias response and independent out-of-loop optical noise not verified'))

    def record(self,label,log):
        self.notify('status',label+'：定时采集/读取缓存')
        y,meta=self.dev.capture(log,self.cancel,lambda s:self.notify('status',label+'：'+s))
        self.counter+=1
        name=f'{self.counter:03d}_{label}'
        np.savez_compressed(self.folder/(name+'.npz'),adc_codes=y,**meta)
        save_json(self.folder/(name+'.json'),dict(label=label,**meta))
        # Always save the original record, including invalid ones.
        if np.any(y<=1) or np.any(y>=16382):raise RuntimeError('ADC 数据接近量程边缘，已保存原始记录，停止测量')
        if label!='dark' and meta['status']&12:
            raise RuntimeError('采集期间出现限幅或环路故障，已保存原始记录，拒绝计算性能')
        return y,meta

    def noise(self,low,high):
        self.dev.require_closed_loop();self.dev.inject(0,0)
        logs=rates(self.dev.raw_fs,low,high)
        dark={};pairs=[]
        self.dev.hold(True)
        if not self.prompt('测量噪声底','反馈已保持当前输出。请只遮住 PD 入光，保持接线和 PD 增益不变，然后继续。'):
            raise InterruptedError('已取消噪声底测量')
        for log in logs:
            y,m=self.record('dark',log)
            dark[log]=spectrum(y,m['fs'],m['raw_fs'],m['decimation'])
        if not self.prompt('恢复光照','噪声底已保存。请恢复 PD 入光，保持原光路与增益，然后继续。'):
            raise InterruptedError('等待恢复光照；未生成开闭环对比')
        self.dev.clear_light_loss();self.dev.hold(False)
        if self.cancel.wait(1):raise InterruptedError()
        self.dev.require_closed_loop()
        for log in logs:
            self.dev.hold(True)
            if self.cancel.wait(.5):raise InterruptedError()
            a,ma=self.record('open',log)
            self.dev.hold(False)
            if self.cancel.wait(1):raise InterruptedError()
            b,mb=self.record('closed',log)
            sa=spectrum(a,ma['fs'],ma['raw_fs'],ma['decimation'])
            sb=spectrum(b,mb['fs'],mb['raw_fs'],mb['decimation'])
            sd=dark[log]
            signal=sa.mean-sd.mean
            if abs(sb.mean-sa.mean)>max(1,.05*abs(signal)):
                raise RuntimeError('开闭环平均光强相差超过 5%，不能作为相同工作点的对比')
            row=dict(f=sa.f,open=relative_density(sa,sd.mean),
                closed=relative_density(sb,sd.mean),floor=relative_density(sd,sd.mean,sb.mean),
                log=log,open_mean=sa.mean,closed_mean=sb.mean,dark_mean=sd.mean)
            pairs.append(row)
            result=self.merge(pairs)
            self.notify('spectrum',result)
            self.save_spectra(result)
        result=self.merge(pairs)
        result['requested_band']=[low,high]
        save_json(self.folder/'spectra_metadata.json',dict(requested_band_Hz=[low,high],
            bands=[{k:v for k,v in p.items() if k not in ['f','open','closed','floor']} for p in pairs]))
        return result

    @staticmethod
    def merge(pairs):
        # Use lower-rate / finer-resolution records through their valid upper
        # limit, then the next band. No interpolation across acquisition gaps.
        result={k:[] for k in ['f','open','closed','floor']};last=-1
        for p in sorted(pairs,key=lambda x:-x['log']):
            take=p['f']>last
            if not take.any():continue
            for k in result:result[k].extend(p[k][take])
            last=float(p['f'][take][-1])
        return {k:np.asarray(v) for k,v in result.items()}

    def save_spectra(self,result):
        np.savetxt(self.folder/'RIN.csv',np.column_stack([result[k] for k in ['f','open','closed','floor']]),
             delimiter=',',header='frequency_Hz,open_RIN_per_Hz,closed_RIN_per_Hz,dark_floor_per_Hz',comments='')

    def rms(self,result,low,high):
        value={k:band_rms(result['f'],result[k],low,high) for k in ['open','closed','floor']}
        value.update(low_Hz=low,high_Hz=high,definition='sqrt(integral of one-sided relative power spectral density)')
        save_json(self.folder/'RMS.json',value)
        return value

    def sweep(self,low,high,amp,points=10):
        self.dev.require_closed_loop()
        if not 0<low<high<=self.dev.raw_fs/20:raise ValueError('扫频范围超出原始采样率的 1/20')
        if not 1<=amp<=16383:raise ValueError('扰动幅度应为正整数 DAC 码')
        rows=[]
        for requested in np.geomspace(low,high,points):
            self.dev.inject(0,0)
            if self.cancel.wait(.5):raise InterruptedError()
            self.dev.hold(True)
            base=self.dev.read(0x3c)
            lo=self.dev.read(0x1e);hi=self.dev.read(0x1f)
            if min(base-lo,hi-base)<2*amp:
                raise ValueError('当前工作点的输出余量不足扰动幅度的两倍，请减小扰动幅度')
            freq=self.dev.inject(float(requested),amp)
            choices=[d for d in range(15) if self.dev.raw_fs/2**d >= 12*freq
                     and 8192*freq/(self.dev.raw_fs/2**d)>=10]
            if not choices:raise ValueError('该频率不能同时满足采样点数与记录周期数')
            log=max(choices)
            if self.cancel.wait(max(.2,10/freq)):raise InterruptedError()
            a,ma=self.record('tone_open',log)
            self.dev.hold(False)
            if self.cancel.wait(max(.5,10/freq)):raise InterruptedError()
            b,mb=self.record('tone_closed',log)
            ta=tone(a,ma['fs'],freq);tb=tone(b,mb['fs'],freq)
            value=rejection(ta,tb)
            # Drift relative to optical DC uses dark estimate when available;
            # otherwise report code means for review instead of guessing offset.
            row=dict(frequency_Hz=freq,requested_Hz=float(requested),amplitude_DAC_codes=amp,
                open_amplitude_ADC_codes=ta['amplitude'],closed_amplitude_ADC_codes=tb['amplitude'],
                open_mean_ADC_codes=ta['mean'],closed_mean_ADC_codes=tb['mean'],
                open_sigma=ta['sigma'],closed_sigma=tb['sigma'],**value)
            rows.append(row)
            with (self.folder/'rejection.csv').open('w',newline='',encoding='utf-8-sig') as f:
                writer=csv.DictWriter(f,fieldnames=list(row));writer.writeheader();writer.writerows(rows)
            db=[r['db'] if r['valid'] else np.nan for r in rows]
            bw=crossing([r['frequency_Hz'] for r in rows],db)
            self.notify('sweep',dict(rows=rows.copy(),bandwidth_Hz=bw))
        self.dev.inject(0,0)
        save_json(self.folder/'bandwidth.json',dict(bandwidth_Hz=bw,
            definition='first upward crossing of 20 log10(closed/open amplitude) = -3 dB',
            status='crossing measured; coarse logarithmic interpolation' if bw else 'no reliable crossing in measured range',
            points=points))
        return rows,bw
