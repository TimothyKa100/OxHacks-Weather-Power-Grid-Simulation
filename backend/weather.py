"""Weather provider boundary. Replace load_profile with a validated forecast adapter.

No API calls occur in the demo. Probabilities are illustrative per exposure window.
"""
import json
from pathlib import Path
import numpy as np

def load_profile():
    return json.loads((Path(__file__).resolve().parents[1]/"data/sample_weather.json").read_text())

def fragility(gust_kmh, rain_mm_h, exposure, hardened=False):
    """Illustrative logistic wind curve, modest rain multiplier; not calibrated."""
    probability = np.clip(.65 / (1 + np.exp(-(np.asarray(gust_kmh)-90)/12)) * exposure * (1+.002*rain_mm_h), 0, .95)
    return probability * (.25 if hardened else 1)

def scenarios(network, intensity, seed, count):
    rng = np.random.default_rng(seed)
    profile = load_profile()
    midpoints = np.array([[(network.buses[e['from']-1]['x']+network.buses[e['to']-1]['x'])/2,
                           (network.buses[e['from']-1]['y']+network.buses[e['to']-1]['y'])/2] for e in network.branches])
    # Shared storm severity correlates line risks. Conditional Bernoulli draws remain independent.
    severity = rng.lognormal(-.5*.09**2, .09, (count,1,1))
    centers = np.array([[w['center_x'],w['center_y']] for w in profile['windows']])
    distance = np.sum((centers[:,None,:]-midpoints[None,:,:])**2,axis=2)
    footprint = .55+.45*np.exp(-distance/.13)
    strength = np.array([w['strength'] for w in profile['windows']])[:,None]
    gust = intensity * footprint[None,:,:] * strength[None,:,:] * severity
    gust = gust * rng.uniform(.94,1.06,(count,1,len(network.branches)))
    draws = rng.random(gust.shape)
    return [{"gust":gust[i],"draws":draws[i],"rain":np.array([w['rain_mm_h'] for w in profile['windows']])} for i in range(count)]
