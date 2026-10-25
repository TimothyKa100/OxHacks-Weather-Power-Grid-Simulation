"""Geographic-grid adapter for the portable weather generator.

Only this layer knows route sample layout, terrain correction and grid playback.
Raw synthetic 10m weather remains separate from engineering pylon gusts.
"""
from functools import lru_cache
import json

import numpy as np

from .benchmark import Projection
from .storm import route_samples, pylon_failure_probability, model_info
from .storm_scenarios import generate_weather, validate_scenario, WARMUP_HOURS
from .simulation import simulate
from .weather import multiday_scenarios


@lru_cache(maxsize=4)
def scenario_context(net, config_json):
    config = validate_scenario(json.loads(config_json))
    samples = route_samples(net)
    projection = Projection(net)
    lon, lat = projection.lonlat(*samples['xy'].T)
    # Weather is generated once per ~5–11 km cell, shared by route samples.
    cells, inverse = np.unique(np.round(np.column_stack([lon, lat]), 1), axis=0, return_inverse=True)
    weather = generate_weather(config, cells)
    variables = {key: value[WARMUP_HOURS:WARMUP_HOURS + config['hours']]
                 for key, value in weather['variables'].items()}
    gust = variables['wind_gusts_10m'][:, inverse] * samples['c_alt'][None, :]
    hazards = -samples['pylons'][None, :] * np.log1p(-np.minimum(pylon_failure_probability(gust), 1 - 1e-12))
    lines = samples['line']
    weights = samples['pylons']
    totals = np.bincount(lines, weights, minlength=net.m)
    timestamps = [t.isoformat() for t in weather['timestamps'][WARMUP_HOURS:WARMUP_HOURS + config['hours']]]
    records = []
    for t in range(config['hours']):
        maximum = np.zeros(net.m)
        np.maximum.at(maximum, lines, gust[t])
        rain = np.bincount(lines, variables['precipitation'][t, inverse] * weights, minlength=net.m) / totals
        probability = -np.expm1(-np.bincount(lines, hazards[t], minlength=net.m))
        records.append({'timestamp': timestamps[t], 'gust_kmh': maximum.tolist(),
                        'rain_mm_h': rain.tolist(), 'failure_probability': probability.tolist()})
    cx, cy = projection.xy(*weather['centres'][WARMUP_HOURS:WARMUP_HOURS + config['hours']].T)
    radius = config['radius_km'] / (111.32 * (projection.y1 - projection.y0))
    source = 'Synthetic user-placed storm; illustrative raw weather and route/pylon engineering fragility'
    return {
        'network': net.payload(), 'input_source': source,
        'frames': [{'hour': t + 1, 'storm': [float(cx[t]), float(cy[t])], 'storm_radius': radius}
                   for t in range(config['hours'])],
        'planning_weather': {'source': source, 'branch_ids': [e['id'] for e in net.branches],
                             'engineering_model': {'name': 'route-pylon-v1', 'parameters': model_info(net)},
                             'hours': records},
        # Compact scenario metadata; full cell histories and warm-up stay on the server.
        'synthetic_weather': {'version': 1, 'storm_config': config, 'warmup_hours': WARMUP_HOURS,
                              'weather_cells': len(cells), 'variables': list(variables),
                              'load_assumption': 'Fixed network average demand',
                              'raw_gust_convention': '10m weather-grid gust; terrain correction only in engineering adapter'},
    }


def run_scenario(net, storm_config, crews=6):
    """One failure/repair realization; no portfolio optimization or Monte Carlo bank."""
    from .planning import map_frames
    config = validate_scenario(storm_config)
    context = scenario_context(net, json.dumps(config, sort_keys=True))
    exposure = context['planning_weather']
    scenario = multiday_scenarios(net, config['peak_gust_kmh'], config['seed'], 1,
                                 config['hours'], exposure, crews, config['preset'])[0]
    playback = simulate(net, scenario, playback=True)
    request = {'scenario': 'synthetic', 'storm_config': config, 'crews': crews,
               'seed': config['seed'], 'hours': config['hours'],
               'peak_gust': config['peak_gust_kmh'], 'storm_type': config['preset']}
    return {**{k: v for k, v in context.items() if k not in ('planning_weather', 'frames')},
            'config': request, 'frames': map_frames(playback, context),
            'metrics': {k: v for k, v in playback.items() if k not in ('frames', 'failed_mask', 'direct_weather_failed_mask')},
            'replay': {'request': request, 'endpoint': '/api/storm-scenarios/runs', 'mode': 'single'}}
