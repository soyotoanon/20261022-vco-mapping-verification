"""DAC control-port nominal sine Vpp from the existing 2026-10-22 DC calibration."""
import math

VOLTS_PER_CODE=0.0006533
CALIBRATION=dict(volts_per_code=VOLTS_PER_CODE,zero_code=8198,date='2026-10-22',
    source='soyotoanon/20261022-vco-mapping-verification README: two-point DMM DC calibration',
    node='DAC output / VCO analog control input',
    meaning='nominal set Vpp from DC calibration, not an AC voltage measurement; includes integer-code rounding')


def codes_to_vpp(peak_codes):
    return 2*VOLTS_PER_CODE*int(peak_codes)


def vpp_to_codes(vpp):
    value=float(vpp)
    if not math.isfinite(value) or not VOLTS_PER_CODE<=value<=codes_to_vpp(16383):
        raise ValueError('扰动 Vpp 超出可设置范围')
    code=int(math.floor(value/(2*VOLTS_PER_CODE)+.5))
    if not 1<=code<=16383:raise ValueError('扰动小于电压分辨率')
    return code
