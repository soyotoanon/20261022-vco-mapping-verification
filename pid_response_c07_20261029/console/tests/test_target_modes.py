"""Exercise the actual UART adapter with in-memory frames; no physical ports."""
from pathlib import Path
import sys, unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Controller, FPGA
from test_channels import FakeSerial

CAL={'wavelength_nm':556,'encoding':'pi_unsigned_14',
     'points':[{'power_mw':0,'adc_code':8000},{'power_mw':20,'adc_code':10000}]}

class TargetTests(unittest.TestCase):
    def setUp(self):
        self.c=Controller(556)
        self.c.closed.set();self.c.thread.join()
        self.f=FPGA('COM99',FakeSerial)
        self.f.serial.registers.update({0:1,0x18:9000,0x2c:1})
        self.c.fpga=self.f
        self.c.state['fpga']=self.f.snapshot()
    def tearDown(self):self.c.close()
    def writes(self):return [(f[2],f[3]+256*f[4]) for f in self.f.serial.writes]
    def test_default_step_is_immediate_and_bypasses_hardware_ramp(self):
        self.c.target(11,None,CAL)
        self.assertEqual(self.writes(),[(0x2c,16383),(0x18,9100)])
        self.assertIsNone(self.c.ramp_job)
        self.assertEqual(self.c.snapshot()['fpga']['setpoint_code'],9100)
    def test_explicit_ramp_uses_only_software_rate(self):
        with patch('server.time.monotonic',return_value=10):self.c.target(11,2,CAL,'ramp')
        self.assertEqual(self.writes(),[(0x2c,16383)])
        with patch('server.time.monotonic',return_value=10.2):self.c.advance_ramp()
        self.assertEqual(self.f.read(0x18),9040)
        with patch('server.time.monotonic',return_value=10.5):self.c.advance_ramp()
        self.assertEqual(self.f.read(0x18),9100)
        self.assertIsNone(self.c.ramp_job)
    def test_step_cancels_pending_ramp_and_never_resumes_it(self):
        self.c.target(12,2,CAL,'ramp');self.c.target(9,None,CAL,'step')
        self.c.advance_ramp()
        self.assertEqual(self.f.read(0x18),8900)
        self.assertIsNone(self.c.ramp_job)
    def test_same_target_still_removes_hidden_limiter(self):
        self.c.target(10,None,CAL)
        self.assertEqual(self.writes(),[(0x2c,16383)])
    def test_step_can_replace_target_outside_new_calibration(self):
        self.f.serial.registers[0x18]=12000
        self.c.target(11,None,CAL)
        self.assertEqual(self.f.read(0x18),9100)
    def test_invalid_requests_leave_registers_untouched(self):
        for args in [(11,2,CAL,'invalid'),(11,0,CAL,'ramp'),(25,2,CAL,'step'),
                     (11,2,{**CAL,'wavelength_nm':399},'step')]:
            with self.assertRaises(ValueError):self.c.target(*args)
        self.assertEqual(self.writes(),[])
    def test_existing_measurement_is_not_overridden(self):
        self.f.serial.registers[0x30]=1
        with self.assertRaises(RuntimeError):self.c.target(11,None,CAL)
        self.assertEqual(self.writes(),[])
    def test_failed_limiter_readback_never_sends_target(self):
        real=self.f.write_verified
        def fail(a,v):
            if a==0x2c:raise RuntimeError('readback mismatch')
            real(a,v)
        with patch.object(self.f,'write_verified',side_effect=fail):
            with self.assertRaises(RuntimeError):self.c.target(11,None,CAL)
        self.assertEqual(self.f.read(0x18),9000)
        self.assertIsNone(self.c.ramp_job)

if __name__=='__main__':unittest.main()
