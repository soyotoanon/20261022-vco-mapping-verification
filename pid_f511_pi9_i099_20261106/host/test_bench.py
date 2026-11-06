"""Offline checks for refusal on wrong firmware and rollback on failed trials."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import bench

class FakeUART:
    version = 0xF511
    instances = []
    def __init__(self, port, log_dir):
        self.regs = {f'{a:02X}':0 for a in range(64)}
        self.regs.update({'00':1,'03':9000,'05':8000,'18':9000,'1A':35072,'1B':50463,
                          '1E':545,'1F':15851,'2E':1,'11':4,'34':self.version,'3F':0xA624})
        self.writes=[]
        self.closed=False
        self.instances.append(self)
    def snapshot(self):return dict(self.regs)
    def read(self, addr):return self.regs[f'{addr:02X}']
    def write(self, addr, value):
        self.writes.append((addr,value))
        if addr != 0x34:self.regs[f'{addr:02X}'] = value & 3 if addr == 0x30 else value
    def close(self):self.closed=True

class Tests(unittest.TestCase):
    def setUp(self):
        FakeUART.instances=[]
        FakeUART.version=0xF511
        self.temp=tempfile.TemporaryDirectory(dir=bench.ROOT/'results')
        self.out=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def test_wrong_firmware_has_no_writes(self):
        FakeUART.version=0xF129
        with patch.object(bench,'UART',FakeUART),patch.object(bench,'console_free'):
            with self.assertRaisesRegex(RuntimeError,'Expected F511'):
                with bench.ProbeSession(self.out):pass
        uart=FakeUART.instances[0]
        self.assertEqual(uart.writes,[])
        self.assertTrue(uart.closed)
    def test_failed_trial_restores_gains_and_probe(self):
        with patch.object(bench,'UART',FakeUART),patch.object(bench,'console_free'):
            with self.assertRaisesRegex(RuntimeError,'simulated acquisition failure'):
                with bench.ProbeSession(self.out) as board:
                    saved=dict(board.original)
                    board.u.write(0x1A,33024)
                    board.u.write(0x1B,32769)
                    board.u.write(0x30,3)
                    board.u.write(0x31,16)
                    raise RuntimeError('simulated acquisition failure')
        uart=FakeUART.instances[0]
        for addr in (0x1A,0x1B,*bench.ProbeSession.probe_regs):
            self.assertEqual(uart.read(addr),saved[f'{addr:02X}'])
        self.assertTrue(uart.closed)
        self.assertTrue(json.loads((self.out/'restoration.json').read_text())['restored'])
    def test_saturation_is_not_accepted_as_lock(self):
        for status in (1,4,8,16,32):
            state={'00':1,'2E':1,'01':status,'1E':545,'1F':15851,'05':8000,'03':9000,'18':9000}
            with self.assertRaises(RuntimeError):bench.require_lock(state)
    def test_tone_fit_removes_dc_and_drift(self):
        fs=625000;frequency=12345;ratio=8;count=4096
        omega=2*np.pi*frequency/bench.FS
        droop=abs(np.sin(omega*ratio/2)/(ratio*np.sin(omega/2)))**3
        t=np.arange(count)/fs
        y=9000+2.5*droop*np.cos(2*np.pi*frequency*t+.7)+30*t
        raw=np.round(y*4).astype(np.uint16)
        fit=bench.fit_tone(raw,{'fs_hz':fs,'decimation':ratio},frequency)
        self.assertAlmostEqual(fit['amplitude_peak_adc_codes'],2.5,delta=.02)

    def test_integral_gain_rounding_at_upper_boundary(self):
        self.assertEqual(bench.ki_word(0.999999999),0xFFFF)
        self.assertEqual(bench.quantize_ki(0.999999999),32767/32768)
        self.assertEqual(bench.ki_word(0),0x8000)
        self.assertEqual(bench.ki_word(.5),0xC000)
        for value in [1,-.01,float('nan'),float('inf')]:
            with self.assertRaises(ValueError):bench.ki_word(value)

if __name__=='__main__':unittest.main(verbosity=2)
