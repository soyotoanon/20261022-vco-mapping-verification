"""One-sided density spectra, band RMS and small-signal rejection analysis."""
from dataclasses import dataclass
import numpy as np
from scipy.signal import welch


def cic_response(f, raw_fs, decimation):
    """Magnitude of a normalized three-stage CIC, including R=1."""
    f = np.asarray(f, dtype=float)
    return np.abs(np.sinc(f * decimation / raw_fs) / np.sinc(f / raw_fs)) ** 3


@dataclass
class Spectrum:
    f: np.ndarray
    density: np.ndarray
    mean: float
    fs: float
    nperseg: int
    averages: int


def spectrum(samples, fs, raw_fs=None, decimation=1, nperseg=2048):
    y = np.asarray(samples, dtype=float)
    if y.ndim != 1 or len(y) < 256 or not np.isfinite(y).all():
        raise ValueError('需要至少 256 个有限、等间隔样本')
    if not np.isfinite(fs) or fs <= 0:
        raise ValueError('采样率必须为正')
    nperseg = min(int(nperseg), len(y))
    f, p = welch(y, fs=fs, window='hann', nperseg=nperseg,
                 noverlap=nperseg // 2, detrend='constant', scaling='density')
    if decimation > 1:
        p /= np.maximum(cic_response(f, raw_fs, decimation) ** 2, 1e-20)
    # Low bins are affected by segment de-meaning; upper limit keeps CIC
    # passband correction small. This is not proof of an alias-free input.
    use = (f >= 3 * fs / nperseg) & (f <= fs / 10)
    return Spectrum(f[use], p[use], float(y.mean()), float(fs), nperseg,
                    1 + (len(y)-nperseg)//(nperseg-nperseg//2))


def relative_density(spec, dark_mean, light_mean=None):
    signal = (spec.mean if light_mean is None else light_mean) - dark_mean
    if not np.isfinite(signal) or abs(signal) < 1:
        raise ValueError('光信号相对暗场不足 1 ADC 码，不能可靠归一化 RIN')
    return spec.density / signal**2


def band_rms(f, density, low, high):
    """Integrate interpolated density at exact band edges; no extrapolation."""
    f, density = np.asarray(f), np.asarray(density)
    if len(f) < 2 or not np.all(np.diff(f) > 0):
        raise ValueError('频率轴必须递增')
    if not (f[0] <= low < high <= f[-1]):
        raise ValueError(f'积分频段须在已测范围 {f[0]:g}–{f[-1]:g} Hz 内')
    if np.any(density < 0) or not np.isfinite(density).all():
        raise ValueError('功率谱必须非负且有限')
    inner = (f > low) & (f < high)
    x = np.r_[low, f[inner], high]
    p = np.r_[np.interp(low, f, density), density[inner], np.interp(high, f, density)]
    return float(np.sqrt(np.trapezoid(p, x)))


def tone(samples, fs, frequency):
    """Fit DC, linear drift, sin and cos; uncertainty from residual variance."""
    y = np.asarray(samples, dtype=float)
    if not 0 < frequency <= fs / 10 or len(y) * frequency / fs < 10:
        raise ValueError('正弦拟合需要每周期至少 10 点、记录至少 10 周期')
    t = np.arange(len(y), dtype=float) / fs
    t -= t.mean()
    a = np.column_stack((np.ones(len(y)), t / max(t[-1], 1/fs),
                         np.sin(2*np.pi*frequency*t), np.cos(2*np.pi*frequency*t)))
    coef, _, _, _ = np.linalg.lstsq(a, y, rcond=None)
    residual = y - a @ coef
    covariance = np.linalg.inv(a.T @ a) * np.dot(residual, residual)/(len(y)-4)
    amplitude = float(np.hypot(coef[2], coef[3]))
    gradient = coef[2:4] / max(amplitude, 1e-30)
    sigma = float(np.sqrt(gradient @ covariance[2:4, 2:4] @ gradient))
    return dict(amplitude=amplitude, sigma=sigma, mean=float(coef[0]),
                residual_rms=float(np.std(residual)),
                detected=bool(amplitude > 5*sigma and amplitude > 0))


def rejection(open_tone, closed_tone):
    if not open_tone['detected']:
        return dict(db=None, valid=False, reason='开环正弦未可靠检出')
    if not closed_tone['detected']:
        return dict(db=None, valid=False, reason='闭环低于拟合检出阈值；不据此插值带宽')
    return dict(db=float(20*np.log10(closed_tone['amplitude']/open_tone['amplitude'])),
                valid=True, reason='')


def crossing(f, db, threshold=-3.0):
    """First upward crossing, only through consecutive valid points."""
    f, db = np.asarray(f, dtype=float), np.asarray(db, dtype=float)
    if len(f) < 2 or np.any(np.diff(f) <= 0):
        return None
    if not np.isfinite(db[0]) or db[0] > threshold:
        return None
    for i in range(1, len(f)):
        if not np.isfinite(db[i-1:i+1]).all():
            return None
        if db[i] >= threshold and db[i-1] < threshold:
            frac = (threshold-db[i-1])/(db[i]-db[i-1])
            return float(np.exp(np.log(f[i-1]) + frac*np.log(f[i]/f[i-1])))
    return None
