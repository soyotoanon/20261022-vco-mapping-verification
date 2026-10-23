import csv,json,time
from dataclasses import asdict
from voltage import codes_to_vpp,CALIBRATION
from pathlib import Path
import numpy as np
from analysis import spectrum,relative_density,band_rms,tone,rejection,crossing,cic_response


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
        self.stage='';self.dark_preview=[];self.dark_mean=None
        self.folder.mkdir(parents=True,exist_ok=True)
        save_json(self.folder/'session.json',dict(registers=dev.snapshot,raw_sample_rate_Hz=dev.raw_fs,
            voltage_calibration=CALIBRATION,detector='in-loop feedback ADC',created=time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            noise_floor='dark PD + ADC; excludes illuminated shot noise',
            spectral_method='one-sided Welch, Hann, 2048 samples, 50% overlap, per-segment DC removal',
            decimator='CIC3; passband correction; retained frequencies <= output_fs/10',
            limitation='Analog anti-alias response and independent out-of-loop optical noise not verified'))

    def record(self,label,log):
        prefix=self.stage or label
        self.notify('status',prefix+'：定时采集/读取缓存')
        y,meta=self.dev.capture(log,self.cancel,lambda s:self.notify('status',prefix+'：'+s))
        self.counter+=1
        name=f'{self.counter:03d}_{label}'
        np.savez_compressed(self.folder/(name+'.npz'),adc_codes=y,**meta)
        save_json(self.folder/(name+'.json'),dict(label=label,**meta))
        if label=='dark':
            sd=spectrum(y,meta['fs'],meta['raw_fs'],meta['decimation'])
            self.dark_preview.append(dict(f=sd.f,density=sd.density,mean=sd.mean,log=log))
            self.notify('dark_spectrum',list(self.dark_preview))
            blocks=np.array_split(y,8)
            if np.ptp([b.mean() for b in blocks])>max(10,10*np.median([b.std() for b in blocks])):
                raise RuntimeError('暗场记录中出现明显变化：请保持 PD 完全遮光直到“恢复光照”提示。该记录已保存，但不用于 RIN。')
        # Always save the original record, including invalid ones.
        if np.any(y<=1) or np.any(y>=16382):raise RuntimeError('ADC 数据接近量程边缘，已保存原始记录，停止测量')
        if label!='dark' and meta['status']&12:
            raise RuntimeError('采集期间出现限幅或环路故障，已保存原始记录，拒绝计算性能')
        return y,meta

    def noise(self,low,high):
        self.dev.require_closed_loop();self.dev.inject(0,0)
        logs=rates(self.dev.raw_fs,low,high)
        dark={};pairs=[];self.dark_preview=[];self.dark_mean=None
        self.dev.hold(True)
        if not self.prompt('测量噪声底','反馈已保持当前输出。请完全遮住 PD 入光，保持接线和 PD 增益不变，再点确定。\n请持续遮光约 2 分钟，直到弹出“恢复光照”；期间会逐段显示暗场曲线。'):
            raise InterruptedError('已取消噪声底测量')
        for i,log in enumerate(logs):
            self.stage=f'暗场 {i+1}/{len(logs)} · 请保持遮光'
            y,m=self.record('dark',log)
            dark[log]=spectrum(y,m['fs'],m['raw_fs'],m['decimation'])
            if np.ptp([s.mean for s in dark.values()])>10:
                raise RuntimeError('各段暗场平均值相差超过 10 ADC 码，请检查是否中途透光；本次不生成 RIN。')
        self.notify('status','暗场已完成：请恢复 PD 入光，并在“恢复光照”提示框中点确定。')
        if not self.prompt('恢复光照','噪声底已保存。请恢复 PD 入光，保持原光路与增益，然后继续。'):
            raise InterruptedError('等待恢复光照；未生成开闭环对比')
        self.dev.clear_light_loss();self.dev.hold(False)
        if self.cancel.wait(1):raise InterruptedError()
        self.dev.require_closed_loop()
        for i,log in enumerate(logs):
            self.stage=f'开环 {i+1}/{len(logs)} · 请保持入光'
            self.dev.hold(True)
            if self.cancel.wait(.5):raise InterruptedError()
            a,ma=self.record('open',log)
            self.stage=f'闭环 {i+1}/{len(logs)} · 请保持入光'
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
        self.dark_mean=float(np.median([v.mean for v in dark.values()]))
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
        if not (0<low<=high<=self.dev.raw_fs/20 and 1<=points<=64 and (low<high or points==1)):raise ValueError('扫频范围超出原始采样率的 1/20')
        if not 1<=amp<=16383:raise ValueError('扰动幅度应为正整数 DAC 码')
        rows=[]
        self.stage=''
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
            for fitted,meta in [(ta,ma),(tb,mb)]:
                gain=float(cic_response(freq,meta['raw_fs'],meta['decimation']))
                fitted['amplitude']/=gain;fitted['sigma']/=gain
            value=rejection(ta,tb)
            # Drift relative to optical DC uses dark estimate when available;
            # otherwise report code means for review instead of guessing offset.
            row=dict(frequency_Hz=freq,requested_Hz=float(requested),amplitude_DAC_codes=amp,amplitude_Vpp=codes_to_vpp(amp),
                open_amplitude_ADC_codes=ta['amplitude'],closed_amplitude_ADC_codes=tb['amplitude'],
                open_mean_ADC_codes=ta['mean'],closed_mean_ADC_codes=tb['mean'],
                open_sigma=ta['sigma'],closed_sigma=tb['sigma'],**value)
            row['open_modulation_percent']=None
            row['closed_residual_percent']=None
            if self.dark_mean is not None:
                dc_open=ta['mean']-self.dark_mean;dc_closed=tb['mean']-self.dark_mean
                if min(dc_open,dc_closed)>1:
                    row['open_modulation_percent']=100*ta['amplitude']/dc_open
                    row['closed_residual_percent']=100*tb['amplitude']/dc_closed
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


    def grid(self,low,high,amp_low,amp_high,freq_points=12,amp_points=5):
        """Measured cells only. Separate run folder; flush after every point."""
        if not (1<=freq_points<=64 and 1<=amp_points<=16):
            raise ValueError('频率点数 1–64；幅度点数 1–16')
        if not (0<low<=high<=self.dev.raw_fs/20) or (freq_points==1)!=(low==high):
            raise ValueError('单频扫幅请将频率上下限设为相同值、频率点数设为 1')
        if not (1<=amp_low<=amp_high<=16383) or (amp_points==1)!=(amp_low==amp_high):
            raise ValueError('扰动电压范围无效；单幅时上下限相同、幅度点数为 1')
        amps=np.unique(np.rint(np.geomspace(amp_low,amp_high,amp_points)).astype(int))
        if len(amps)!=amp_points:raise ValueError('幅度范围小于电压分辨率，取整后重复，请减少幅度点数')
        frequencies=np.geomspace(low,high,freq_points)
        parent=self.folder;notify=self.notify
        folder=parent/('grid_'+str(time.time_ns()));folder.mkdir()
        data=dict(frequencies_Hz=frequencies.tolist(),amplitudes_DAC_codes=amps.tolist(),
                  amplitudes_Vpp=[codes_to_vpp(a) for a in amps],voltage_calibration=CALIBRATION,
                  rows=[],bandwidths_Hz=[None]*len(amps),status='running',run_directory=folder.name,
                  dark_mean_ADC_codes=self.dark_mean,
                  definition='20 log10(closed/open fundamental amplitude); percent = peak tone / dark-subtracted DC',
                  note='In-loop sequential measurements; no assumption of a unique monotone -3 dB boundary')
        def flush():
            save_json(folder/'grid.json',data)
            if data['rows']:
                keys=sorted(set().union(*(r.keys() for r in data['rows'])))
                with (folder/'grid.csv').open('w',newline='',encoding='utf-8-sig') as f:
                    writer=csv.DictWriter(f,fieldnames=keys);writer.writeheader();writer.writerows(data['rows'])
            notify('grid',json.loads(json.dumps(data)))
        flush()
        completed=[];ai=0;row_count=0
        try:
            for ai,amp in enumerate(amps):
                row_count=0
                self.folder=folder/f'amp_{int(amp)}';self.folder.mkdir()
                def relay(kind,payload):
                    nonlocal row_count
                    if kind=='sweep':
                        row_count=len(payload['rows'])
                        data['rows']=completed+[dict(r,amplitude_index=ai,frequency_index=i)
                                              for i,r in enumerate(payload['rows'])]
                        data['bandwidths_Hz'][ai]=payload['bandwidth_Hz'];flush()
                    elif kind=='status':
                        notify(kind,f'二维扫描 {len(completed)+row_count+1}/{len(amps)*freq_points} · {codes_to_vpp(amp):.5g} Vpp · '+payload)
                    else:notify(kind,payload)
                self.notify=relay
                self.sweep(low,high,int(amp),freq_points)
                completed=list(data['rows'])
            data['status']='complete';flush()
        except Exception as e:
            data['status']='cancelled' if isinstance(e,InterruptedError) else 'failed'
            data['error']=str(e)
            data['interrupted_cell']=dict(amplitude_index=ai,frequency_index=row_count)
            flush();raise
        finally:
            self.folder=parent;self.notify=notify
            self.dev.inject(0,0);self.dev.hold(False)
        return data


    def extend_grid(self,source_grid,low,high,amp_low,amp_high,freq_points=13,amp_points=5):
        """Reuse completed cells with identical settings; retain raw provenance."""
        source=Path(source_grid).resolve();seed=json.loads(source.read_text(encoding='utf-8'))
        if seed['status']=='running':raise ValueError('原扫描仍在运行，请先等待它结束')
        old_session=json.loads((source.parent.parent/'session.json').read_text(encoding='utf-8'))
        keys=['0x0','0x11','0x18','0x19','0x1a','0x1b','0x1c','0x1d','0x1e','0x1f','0x20','0x21','0x2d','0x2e','0x3f']
        if any(k not in old_session['registers'] or k not in self.dev.snapshot or old_session['registers'][k]!=self.dev.snapshot[k] for k in keys):
            raise ValueError('PID/采样配置改变，不能自动合并旧测量')
        if old_session['raw_sample_rate_Hz']!=self.dev.raw_fs or seed.get('voltage_calibration')!=CALIBRATION:
            raise ValueError('采样率或电压标定改变，不能复用')
        if not (0<low<high<=self.dev.raw_fs/20 and 2<=freq_points<=64 and 2<=amp_points<=16 and 1<=amp_low<amp_high<=16383):
            raise ValueError('加密网格范围或点数无效')
        frequencies=np.geomspace(low,high,freq_points)
        amps=np.unique(np.rint(np.geomspace(amp_low,amp_high,amp_points)).astype(int))
        if len(amps)!=amp_points:raise ValueError('幅度取整后重复，请减少幅度点数')
        parent=self.folder;notify=self.notify
        folder=parent/('grid_'+str(time.time_ns()));folder.mkdir()
        cells={}
        for r in seed['rows']:
            fi=np.flatnonzero(np.isclose(frequencies,r['requested_Hz'],rtol=1e-9,atol=1e-12))
            ai=np.flatnonzero(amps==r['amplitude_DAC_codes'])
            if len(fi)==1 and len(ai)==1:
                row=dict(r,frequency_index=int(fi[0]),amplitude_index=int(ai[0]),reused=True)
                row.setdefault('source_grid',str(source))
                row.setdefault('source_raw_directory',str(source.parent/f"amp_{r['amplitude_DAC_codes']}"))
                cells[int(ai[0]),int(fi[0])]=row
        reused=len(cells)
        data=dict(frequencies_Hz=frequencies.tolist(),amplitudes_DAC_codes=amps.tolist(),
            amplitudes_Vpp=[codes_to_vpp(a) for a in amps],voltage_calibration=CALIBRATION,
            rows=[],bandwidths_Hz=[None]*len(amps),status='running',run_directory=folder.name,
            reused_points=reused,source_grid=str(source),dark_mean_ADC_codes=None,
            note='Prior raw data retained at source paths. Reused cells are flagged; same optical setup assumed.')
        def flush():
            data['rows']=[cells[k] for k in sorted(cells)]
            for i in range(len(amps)):
                db=[cells[i,j]['db'] if (i,j) in cells and cells[i,j]['valid'] else np.nan for j in range(len(frequencies))]
                data['bandwidths_Hz'][i]=crossing(frequencies,db)
            save_json(folder/'grid.json',data)
            if cells:
                keys=sorted(set().union(*(r.keys() for r in data['rows'])))
                with (folder/'grid.csv').open('w',newline='',encoding='utf-8-sig') as f:
                    writer=csv.DictWriter(f,fieldnames=keys);writer.writeheader();writer.writerows(data['rows'])
            notify('grid',json.loads(json.dumps(data)))
        flush();ai=fi=0
        try:
            for ai,amp in enumerate(amps):
                for fi,freq in enumerate(frequencies):
                    if (ai,fi) in cells:continue
                    self.dev.require_closed_loop()
                    self.folder=folder/f'amp_{int(amp)}'/f'frequency_{fi:03d}'
                    self.folder.mkdir(parents=True)
                    def relay(kind,payload):
                        if kind=='sweep':return
                        if kind=='status':payload=f'加密扫描 {len(cells)+1}/{len(amps)*len(frequencies)} · 已复用 {reused} 点 · {codes_to_vpp(amp):.5g} Vpp · '+payload
                        notify(kind,payload)
                    self.notify=relay
                    rows,_=self.sweep(float(freq),float(freq),int(amp),1)
                    cells[ai,fi]=dict(rows[0],amplitude_index=ai,frequency_index=fi,reused=False,
                        source_grid=str(folder/'grid.json'),source_raw_directory=str(self.folder))
                    flush()
            data['status']='complete';flush()
        except Exception as e:
            data['status']='cancelled' if isinstance(e,InterruptedError) else 'failed'
            data['error']=str(e);data['interrupted_cell']=dict(amplitude_index=ai,frequency_index=fi)
            flush();raise
        finally:
            self.folder=parent;self.notify=notify
            self.dev.inject(0,0);self.dev.hold(False)
        return data
