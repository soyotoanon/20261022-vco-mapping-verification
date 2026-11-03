"""Step settling uses the response span, not the DC operating power."""
import numpy as np
from analyze_results import measures as legacy_measures

def measures(t,power,target,hold_s):
    t=np.asarray(t);power=np.asarray(power)
    baseline=float(np.median(power[t<0]));span=abs(target-baseline)
    result=legacy_measures(t,power,target,hold_s,tolerance_fraction=.02*span/abs(target))
    result['tolerance_definition']='plus/minus 2 percent of actual response span (target minus pre-step median)'
    result['tolerance_fraction_of_step']=.02
    result['analysis_revision']='step-span-2percent-20261103'
    if result['settling_s'] is not None:
        assert result['settling_s']>=result['t90_s']
        assert np.all(abs(power[t>=result['settling_s']]-target)<=result['tolerance_mw'])
    return result
