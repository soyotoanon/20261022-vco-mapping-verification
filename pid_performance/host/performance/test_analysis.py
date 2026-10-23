import unittest
import numpy as np
from analysis import spectrum, relative_density, band_rms, tone, rejection, crossing

class AnalysisTests(unittest.TestCase):
    def test_white_noise_density_and_rms(self):
        rng=np.random.default_rng(412)
        fs=10000
        y=8300+rng.normal(0,2,262144)
        s=spectrum(y,fs)
        p=relative_density(s,8192)
        measured=band_rms(s.f,p,100,900)
        expected=2/108*np.sqrt(2*800/fs)
        self.assertAlmostEqual(measured/expected,1,delta=.035)
    def test_dark_floor_uses_illuminated_mean(self):
        s=spectrum(8192+np.random.default_rng(5).normal(size=8192),10000)
        p=relative_density(s,8192,8292)
        np.testing.assert_allclose(p,s.density/10000)
        with self.assertRaises(ValueError):relative_density(s,8192)
    def test_noncoherent_sine_and_drift(self):
        fs=10000;t=np.arange(8192)/fs
        y=8300+8*np.sin(2*np.pi*137.23*t+.8)+.4*t
        r=tone(y,fs,137.23)
        self.assertAlmostEqual(r['amplitude'],8,places=8)
        q=tone(8300+.8*np.sin(2*np.pi*137.23*t),fs,137.23)
        self.assertAlmostEqual(rejection(r,q)['db'],-20,places=8)
    def test_band_edges(self):
        self.assertAlmostEqual(band_rms([1,2,3],[2,2,2],1.25,2.75),np.sqrt(3))
        with self.assertRaises(ValueError):band_rms([1,2,3],[2,2,2],0,2)
    def test_no_invented_bandwidth(self):
        self.assertAlmostEqual(crossing([100,1000],[-6,0]),np.sqrt(100000))
        self.assertIsNone(crossing([100,200,300],[-20,np.nan,0]))
        self.assertIsNone(crossing([100,200],[-20,-10]))
        self.assertIsNone(crossing([100,200],[0,3]))

if __name__=='__main__':unittest.main()
