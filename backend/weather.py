"""Weather provider boundary. Replace load_profile with a validated forecast adapter.

No API calls occur in the demo. Probabilities are illustrative per exposure window.
"""
import json
from pathlib import Path
import numpy as np

STORM_PRESETS = {
    'single_peak': 'Windstorm followed by recovery',
    'persistent': 'Prolonged windstorm',
    'double_peak': 'Two successive windstorm fronts',
}

def load_profile():
    return json.loads((Path(__file__).resolve().parents[1]/"data/sample_weather.json").read_text())

def fragility(gust_kmh, rain_mm_h, exposure, hardened=False):
    """Illustrative logistic wind curve, modest rain multiplier; not calibrated."""
    probability = np.clip(.65 / (1 + np.exp(-(np.asarray(gust_kmh)-90)/12)) * exposure * (1+.002*rain_mm_h), 0, .95)
    return probability * (.25 if hardened else 1)

def scenarios(network, intensity, seed, count):
    rng = np.random.default_rng(seed)
    profile = load_profile()
    midpoints = network.midpoints
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


def failure_probability(gust, rain, exposure, dt_hours=1., hardened=False):
    """Illustrative hourly hazard; hardening reduces rate, not time-step probability."""
    reference = fragility(gust, rain, exposure)
    rate = -np.log1p(-reference) * (.25 if hardened else 1.)
    return -np.expm1(-rate * dt_hours)


def validate_weather(profile, branches):
    """Bounded hourly route-exposure series. Never infer geography for IEEE case14."""
    from datetime import datetime, timezone, timedelta
    if not isinstance(profile, dict) or not isinstance(profile.get('source'), str) or not profile['source'].strip():
        raise ValueError('weather requires a source description')
    hours = profile.get('hours')
    if not isinstance(hours, list) or not 1 <= len(hours) <= 72:
        raise ValueError('weather.hours must contain 1–72 hourly records')
    previous = None
    supplied_predictions = [isinstance(row, dict) and 'failure_probability' in row for row in hours]
    prediction_dates = {}
    engineering = profile.get('engineering_model')
    if engineering is not None and (not isinstance(engineering,dict) or engineering.get('name')!='route-pylon-v1'):
        raise ValueError('Unknown engineering probability model')
    if any(supplied_predictions):
        if not all(supplied_predictions): raise ValueError('Supply failure_probability for every hour or none')
        branch_ids = profile.get('branch_ids')
        if not isinstance(branch_ids,list) or len(branch_ids)!=branches or any(not isinstance(i,str) for i in branch_ids) or len(set(branch_ids))!=branches:
            raise ValueError('ML exposures require ordered, unique branch_ids for every branch')
        prediction = profile.get('prediction')
        if engineering is not None and prediction is not None:
            raise ValueError('Use engineering_model or prediction provenance, not both')
    if any(supplied_predictions) and engineering is None:
        prediction = profile.get('prediction')
        if not isinstance(prediction, dict) or any(not isinstance(prediction.get(k), str) or not prediction[k].strip()
                                                   for k in ('model_version', 'training_cutoff', 'issued_at')):
            raise ValueError('ML exposures require prediction model_version, training_cutoff and issued_at')
        for key in ('training_cutoff', 'issued_at'):
            try:
                stamp = datetime.fromisoformat(prediction[key].replace('Z', '+00:00'))
                if stamp.tzinfo is None: raise ValueError()
                prediction_dates[key] = stamp
            except (ValueError, TypeError): raise ValueError(f'prediction.{key} must be an ISO timestamp with timezone')
        if prediction_dates['training_cutoff'] > prediction_dates['issued_at']:
            raise ValueError('Prediction training cutoff must not follow issue time')
    for row in hours:
        if not isinstance(row, dict): raise ValueError('Each weather hour must be an object')
        try:
            stamp = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ValueError('Each weather hour requires an ISO timestamp with timezone')
        if stamp.tzinfo is None: raise ValueError('Weather timestamps require a timezone')
        stamp = stamp.astimezone(timezone.utc)
        if previous is None and prediction_dates and prediction_dates['issued_at'] > stamp:
            raise ValueError('Predictions must be issued by the start of the replay')
        if previous is not None and stamp - previous != timedelta(hours=1):
            raise ValueError('Weather records must be consecutive hourly timestamps')
        previous = stamp
        for key, maximum in [('gust_kmh', 600), ('rain_mm_h', 300)]:
            value = row.get(key)
            if isinstance(value, bool): raise ValueError(f'Invalid {key}')
            try: array = np.asarray(value, dtype=float)
            except (TypeError, ValueError): raise ValueError(f'Invalid {key}')
            if array.shape not in [(), (branches,)] or not np.all(np.isfinite(array)) or np.any(array < 0) or np.any(array > maximum):
                raise ValueError(f'{key} must be a number or {branches} route values between 0 and {maximum}')
        if any(supplied_predictions):
            value = row['failure_probability']
            try: probabilities = np.asarray(value, dtype=float)
            except (ValueError, TypeError): raise ValueError('Invalid failure_probability')
            if probabilities.shape != (branches,) or not np.all(np.isfinite(probabilities)) or np.any(probabilities < 0) or np.any(probabilities > 1):
                raise ValueError(f'failure_probability requires {branches} values between 0 and 1')
    return profile


def multiday_scenarios(network, intensity, seed, count, hours=48, weather=None, crews=2, storm_type='single_peak'):
    from datetime import datetime, timezone, timedelta
    if storm_type not in STORM_PRESETS:
        raise ValueError('Unknown storm preset')
    rng = np.random.default_rng(seed)
    if weather is not None:
        validate_weather(weather, network.m)
        if 'branch_ids' in weather and weather['branch_ids'] != [e['id'] for e in network.branches]:
            raise ValueError('Weather branch_ids must match /api/network branch order')
        rows = weather['hours']; hours = len(rows)
        gust = np.array([np.broadcast_to(r['gust_kmh'], (network.m,)) for r in rows])
        rain = np.array([np.broadcast_to(r['rain_mm_h'], (network.m,)) for r in rows])
        timestamps = [r['timestamp'] for r in rows]
    elif network.geographic:
        gust, rain = storm_track(network, intensity, hours, storm_type)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        timestamps = [(start+timedelta(hours=int(t))).isoformat() for t in range(hours)]
    else:
        t = np.arange(hours)
        # One storm followed by a calmer recovery period; illustrative route variation.
        envelope = .18 + .82 * np.exp(-.5*((t-hours*.3)/max(2., hours*.12))**2)
        if storm_type == 'persistent':
            envelope = .18 + .82 * np.exp(-.5*((t-hours*.4)/max(2., hours*.24))**4)
        elif storm_type == 'double_peak':
            envelope = .18 + .82 * np.maximum(
                np.exp(-.5*((t-hours*.22)/max(1., hours*.08))**2),
                .95*np.exp(-.5*((t-hours*.58)/max(1., hours*.10))**2))
        gust = intensity * envelope[:, None] * np.linspace(.8, 1., network.m)[None, :]
        rain = np.broadcast_to((20*envelope)[:, None], gust.shape).copy()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        timestamps = [(start+timedelta(hours=int(t))).isoformat() for t in range(hours)]
    # Historical replay keeps the supplied weather fixed. Uncertainty is in failures/repairs.
    draws = rng.random((count, hours, network.m))
    repair = np.clip(rng.lognormal(np.log(8), .4, (count, hours, network.m)), 2, 36)
    result = [dict(gust=gust, rain=rain, draws=draws[i], repair_hours=repair[i],
                   timestamps=timestamps, dt_hours=1., crews=crews, recovery=True) for i in range(count)]
    if weather is not None and 'failure_probability' in rows[0]:
        probabilities = np.array([row['failure_probability'] for row in rows])
        for scenario in result: scenario['failure_probability'] = probabilities
    return result


def storm_track(network, intensity, hours, storm_type='single_peak'):
    """Synthetic Atlantic depression crossing a geographic network south-west to north-east.

    Gust at each branch midpoint falls off with distance from the moving storm
    centre (normalised map units; ~130 km sigma for GB). The storm preset shapes
    how strong it is over time, as in the schematic multi-day envelope. Illustrative only.
    """
    t = np.arange(hours)
    x0, y0 = network.midpoints.min(axis=0); x1, y1 = network.midpoints.max(axis=0)
    span = max(6., hours*.35); peak = hours*.3
    progress = np.clip((t-(peak-span/2))/span, 0, 1)[:, None]
    centre = np.hstack([x0+(x1-x0)*(.1+.8*progress), y1-(y1-y0)*(.1+.8*progress)])
    distance = np.sum((centre[:, None, :]-network.midpoints[None, :, :])**2, axis=2)
    if storm_type == 'persistent':
        active = np.exp(-.5*((t-hours*.4)/max(3., hours*.24))**4)
    elif storm_type == 'double_peak':
        active = np.maximum(np.exp(-.5*((t-hours*.22)/max(1., hours*.08))**2),
                            .95*np.exp(-.5*((t-hours*.58)/max(1., hours*.10))**2))
    else:
        active = np.exp(-.5*((t-peak)/max(3., span*.6))**2)
    active = active[:, None]
    footprint = np.exp(-distance/(2*.12**2))
    gust = intensity * (.2 + .8*footprint*active)
    rain = 25 * footprint * active
    return gust, rain
