import tempfile,unittest
from pathlib import Path
import numpy as np
from measurement import Measurement

class Immediate:
    def is_set(self):return False
    def wait(self,seconds):return False

class SimulatedDevice:
    raw_fs=5e6
    snapshot={'test_only':True}
    def __init__(self):self.dark=False;self.held=False;self.freq=0;self.amp=0;self.seq=0
    def require_closed_loop(self):pass
    def hold(self,value):self.held=value
    def clear_light_loss(self):pass
    def inject(self,f,a):self.freq=f;self.amp=a;return f
    def read(self,a):return {0x3c:6860,0x1e:1400,0x1f:15000}[a]
    def capture(self,log,cancel,progress):
        self.seq+=1;fs=self.raw_fs/2**log;t=np.arange(8192)/fs
        y=np.random.default_rng(self.seq).normal(0,.02,8192)+8192
        if not self.dark:
            y+=1651
            if self.freq:
                gain=1 if self.held else self.freq/np.hypot(self.freq,100)
                y+=self.amp*.01*gain*np.sin(2*np.pi*self.freq*t+.3)
            else:y+=np.random.default_rng(self.seq+100).normal(0,2 if self.held else .3,8192)
        return y,dict(fs=fs,raw_fs=self.raw_fs,decimation=2**log,status=2,sequence=self.seq)

class FlowTest(unittest.TestCase):
    def test_noise_rms_and_sweep_save_originals(self):
        dev=SimulatedDevice();events=[]
        def prompt(title,text):dev.dark=title=='测量噪声底';return True
        with tempfile.TemporaryDirectory() as folder:
            run=Measurement(dev,folder,Immediate(),lambda k,d:events.append(k),prompt)
            spec=run.noise(.5,100000)
            rms=run.rms(spec,.5,100000)
            self.assertGreater(rms['open'],5*rms['closed'])
            rows,bw=run.sweep(.5,100000,128)
            self.assertIsNotNone(bw);self.assertLess(abs(bw-100),50)
            self.assertEqual(len(rows),10)
            self.assertTrue((Path(folder)/'RIN.csv').exists())
            self.assertTrue((Path(folder)/'RMS.json').exists())
            self.assertGreaterEqual(len(list(Path(folder).glob('*.npz'))),29)
            self.assertEqual(events.count('sweep'),10)
            self.assertEqual(events.count('dark_spectrum'),5)
            self.assertLess(events.index('dark_spectrum'),events.index('spectrum'))

    def test_dark_light_transition_is_shown_saved_and_rejected(self):
        class TransitionDevice(SimulatedDevice):
            def capture(self,log,cancel,progress):
                y,m=super().capture(log,cancel,progress)
                y[len(y)//2:]+=1000
                return y,m
        dev=TransitionDevice();events=[]
        def prompt(title,text):dev.dark=True;return True
        with tempfile.TemporaryDirectory() as folder:
            run=Measurement(dev,folder,Immediate(),lambda k,d:events.append(k),prompt)
            with self.assertRaisesRegex(RuntimeError,'暗场记录中出现明显变化'):
                run.noise(.5,100000)
            self.assertIn('dark_spectrum',events)
            self.assertEqual(len(list(Path(folder).glob('*dark.npz'))),1)
            self.assertFalse((Path(folder)/'RIN.csv').exists())

if __name__=='__main__':unittest.main()
