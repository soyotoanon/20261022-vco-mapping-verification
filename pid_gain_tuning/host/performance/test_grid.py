import json,tempfile,unittest
from pathlib import Path
import numpy as np
from measurement import Measurement
from heatmap import matrices
from test_measurement import SimulatedDevice,Immediate


class GridTests(unittest.TestCase):
    def test_two_amplitudes_preserve_rows_and_percent(self):
        dev=SimulatedDevice();events=[]
        with tempfile.TemporaryDirectory() as tmp:
            m=Measurement(dev,tmp,Immediate(),lambda k,d:events.append((k,d)),lambda *a:True)
            m.dark_mean=8192
            d=m.grid(20,1000,32,64,4,2)
            self.assertEqual(d['status'],'complete');self.assertEqual(len(d['rows']),8)
            self.assertEqual(d['amplitudes_Vpp'],[.0418112,.0836224])
            self.assertAlmostEqual(d['rows'][0]['amplitude_Vpp'],.0418112)
            self.assertTrue(all(b is not None for b in d['bandwidths_Hz']))
            db,pct=matrices(d);self.assertEqual(db.shape,(2,4))
            self.assertTrue(np.isfinite(pct).all())
            self.assertGreater(pct[1,-1],1.8*pct[0,-1])
            run=next(Path(tmp).glob('grid_*'))
            self.assertEqual(len(list(run.glob('amp_*/*.npz'))),16)
            self.assertEqual(len((run/'grid.csv').read_text(encoding='utf-8-sig').splitlines()),9)
            self.assertEqual(m.folder,Path(tmp));self.assertFalse(dev.held);self.assertEqual(dev.freq,0)
            self.assertTrue(any(k=='grid' and len(x['rows'])==1 for k,x in events))

    def test_abort_keeps_completed_cells_and_restores_loop(self):
        class Fails(SimulatedDevice):
            def capture(self,*a):
                if self.seq>=3:raise RuntimeError('injected transport failure')
                return super().capture(*a)
        dev=Fails()
        with tempfile.TemporaryDirectory() as tmp:
            m=Measurement(dev,tmp,Immediate(),lambda *a:None,lambda *a:True)
            with self.assertRaisesRegex(RuntimeError,'transport failure'):m.grid(20,1000,32,64,3,2)
            d=json.loads(next(Path(tmp).glob('grid_*/grid.json')).read_text())
            self.assertEqual(d['status'],'failed');self.assertEqual(len(d['rows']),1)
            db,p=matrices(d);self.assertEqual(np.isfinite(db).sum(),1)
            self.assertTrue(np.isnan(p).all())
            self.assertFalse(dev.held);self.assertEqual(dev.freq,0)

    def test_fixed_frequency_amplitude_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=Measurement(SimulatedDevice(),tmp,Immediate(),lambda *a:None,lambda *a:True)
            d=m.grid(100,100,16,64,1,3)
            self.assertEqual(len(d['rows']),3);self.assertEqual(d['bandwidths_Hz'],[None]*3)
            self.assertTrue(all(r['closed_residual_percent'] is None for r in d['rows']))

    def test_extension_reuses_cells_without_recapturing_them(self):
        dev=SimulatedDevice();dev.snapshot={hex(i):0 for i in range(64)}
        with tempfile.TemporaryDirectory() as tmp:
            first=Measurement(dev,Path(tmp)/'old',Immediate(),lambda *a:None,lambda *a:True)
            first.grid(1,100,16,64,2,2)
            source=next((Path(tmp)/'old').glob('grid_*/grid.json'))
            before=dev.seq
            second=Measurement(dev,Path(tmp)/'new',Immediate(),lambda *a:None,lambda *a:True)
            data=second.extend_grid(source,1,100,16,64,3,3,frequencies_Hz=[1,4,100])
            self.assertEqual(data['frequencies_Hz'],[1,4,100])
            self.assertEqual(data['reused_points'],4)
            self.assertEqual(len(data['rows']),9)
            self.assertEqual(dev.seq-before,10) # only five new cells, two captures each
            self.assertEqual(sum(r['reused'] for r in data['rows']),4)
            self.assertTrue(all(Path(r['source_raw_directory']).exists() for r in data['rows']))
            dev.snapshot['0x18']=123
            with self.assertRaisesRegex(ValueError,'配置改变'):
                second.extend_grid(source,1,100,16,64,3,3)

if __name__=='__main__':unittest.main()
