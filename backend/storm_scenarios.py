"""Portable synthetic weather fields. No Flask, network, pandas or frontend dependency.

Coordinates are [longitude, latitude], distances km, winds km/h and directions
meteorological degrees FROM north. These are illustrative scenarios, not forecasts.
"""
from datetime import datetime, timedelta, timezone
import math

import numpy as np

VERSION = 1
WARMUP_HOURS = 72
PRESETS = {
    'single_peak': {'label': 'Windstorm', 'rain_mm_h': 8., 'temperature_drop_c': 3., 'gust_factor': 1.65},
    'persistent': {'label': 'Prolonged wind and rain', 'rain_mm_h': 16., 'temperature_drop_c': 2., 'gust_factor': 1.5},
    'double_peak': {'label': 'Two successive fronts', 'rain_mm_h': 12., 'temperature_drop_c': 4., 'gust_factor': 1.7},
}
DEFAULT_SCENARIO = {
    'version': VERSION, 'origin': {'lon': -10., 'lat': 50.5},
    'bearing_deg': 45., 'speed_kmh': 35., 'radius_km': 150.,
    'peak_gust_kmh': 160., 'hours': 36, 'preset': 'single_peak', 'seed': 1,
    'start_time': '2026-01-01T00:00:00+00:00', 'initial_soil_moisture': .3,
}
LIMITS = {
    'bearing_deg': (0, 360), 'speed_kmh': (5, 100), 'radius_km': (30, 400),
    'peak_gust_kmh': (60, 250), 'hours': (6, 72), 'seed': (0, 2147483647),
    'initial_soil_moisture': (.05, .55),
}


def validate_scenario(value):
    """Normalize a bounded v1 contract; reject typos instead of ignoring controls."""
    if not isinstance(value, dict):
        raise ValueError('storm_config must be an object')
    unknown = set(value) - set(DEFAULT_SCENARIO)
    if unknown:
        raise ValueError('Unknown storm_config fields: ' + ', '.join(sorted(unknown)))
    config = {**DEFAULT_SCENARIO, **value}
    if type(config['version']) is not int or config['version'] != VERSION:
        raise ValueError('Unsupported storm_config version')
    if not isinstance(config['preset'], str) or config['preset'] not in PRESETS:
        raise ValueError('Unknown storm preset')
    for name, (low, high) in LIMITS.items():
        number = config[name]
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or not math.isfinite(number) or not low <= number <= high):
            raise ValueError(f'{name} must be a finite number between {low} and {high}')
        if name in ('hours', 'seed') and type(number) is not int:
            raise ValueError(f'{name} must be an integer')
    origin = config['origin']
    if not isinstance(origin, dict) or set(origin) != {'lon', 'lat'}:
        raise ValueError('origin requires lon and lat')
    for name, low, high in [('lon', -25, 15), ('lat', 45, 65)]:
        number = origin[name]
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or not math.isfinite(number) or not low <= number <= high):
            raise ValueError(f'origin.{name} must be between {low} and {high}')
    try:
        start = datetime.fromisoformat(config['start_time'].replace('Z', '+00:00'))
        if start.tzinfo is None or start.minute or start.second or start.microsecond:
            raise ValueError()
        start = start.astimezone(timezone.utc)
        if start.minute:
            raise ValueError()
        start - timedelta(hours=WARMUP_HOURS)
        start + timedelta(hours=config['hours'])
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise ValueError('start_time must be an aligned ISO hour with a timezone') from None
    return {**config, 'origin': dict(origin), 'start_time': start.isoformat()}


def track(config, hours):
    """Local equirectangular track, shared with the JS preview (clockwise from north)."""
    h = np.asarray(hours, dtype=float)
    angle = np.deg2rad(config['bearing_deg'])
    lon, lat = config['origin']['lon'], config['origin']['lat']
    distance = h * config['speed_kmh']
    return np.column_stack([lon + distance * np.sin(angle) / (111.32 * np.cos(np.deg2rad(lat))),
                            lat + distance * np.cos(angle) / 111.32])


def generate_weather(scenario, locations):
    """Hourly raw fields at N lon/lat locations, including 72h warm-up and final cutoff.

    Instantaneous variables at t describe t; precipitation at t is [t-1h,t),
    evaluated at its midpoint. Warm-up is seasonal background weather. Spatial
    noise depends on coordinates and seed, so batching/reordering locations is safe.
    """
    config = validate_scenario(scenario)
    points = np.asarray(locations, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
        raise ValueError('locations must be finite [lon, lat] pairs')
    if len(points) > 20000:
        raise ValueError('Generate at most 20000 shared weather locations per batch')
    h = np.arange(-WARMUP_HOURS, config['hours'] + 1)
    start = datetime.fromisoformat(config['start_time'])
    timestamps = [start + timedelta(hours=int(t)) for t in h]
    angle = np.deg2rad(config['bearing_deg'])
    cos_lat = np.cos(np.deg2rad(config['origin']['lat']))
    radius = config['radius_km']

    def footprint(times, offset=0.):
        centres = track(config, times)
        east = (points[None, :, 0] - centres[:, None, 0]) * 111.32 * cos_lat
        north = (points[None, :, 1] - centres[:, None, 1]) * 111.32
        along = east * np.sin(angle) + north * np.cos(angle)
        across = east * np.cos(angle) - north * np.sin(angle)
        field = np.exp(-.5 * (((along - offset) / (radius * 1.4)) ** 2 + (across / radius) ** 2))
        phase = np.clip(times / max(1, config['hours'] - 1), 0, 1)
        if config['preset'] == 'persistent':
            strength = .8 + .2 * np.sin(np.pi * phase)
        elif config['preset'] == 'double_peak':
            # Two distinct spatial fronts travelling the same track.
            second = np.exp(-.5 * (((along - offset + radius * 3) / radius) ** 2 + (across / radius) ** 2))
            field = np.maximum(field, .9 * second)
            strength = .8 + .2 * np.sin(np.pi * phase)
        else:
            strength = .65 + .35 * np.sin(np.pi * phase)
        return field * strength[:, None] * (times >= 0)[:, None], east, north

    field, east, north = footprint(h)
    rain_field, _, _ = footprint(h - .5, offset=radius * .6)
    preset = PRESETS[config['preset']]
    # Smooth bounded variation, shared by neighbouring locations; no white-noise storm pixels.
    phase = points[:, 0] * 1.7 + points[:, 1] * 2.3 + (config['seed'] % 10007) * .13
    variation = 1 + .025 * np.sin(h[:, None] * .35 + phase[None, :])
    gust = 18 + (config['peak_gust_kmh'] - 18) * field * variation
    gust = np.clip(gust, 0, config['peak_gust_kmh'])
    wind = gust / preset['gust_factor']
    # Counter-clockwise airflow around a northern hemisphere low, reported as FROM.
    direction = (np.degrees(np.arctan2(-north, east)) + 180) % 360
    direction = np.where(field > .03, direction, (config['bearing_deg'] + 180) % 360)
    direction = np.rint(direction) % 360  # existing exact integer-degree crosswind envelope
    rainfall = preset['rain_mm_h'] * rain_field
    seasonal = np.array([9 - 6 * np.cos(2 * np.pi * (t.timetuple().tm_yday - 15) / 365.25) for t in timestamps])
    daily = np.array([1.5 * np.sin(2 * np.pi * (t.hour - 9) / 24) for t in timestamps])
    temperature = (seasonal + daily)[:, None] - .35 * (points[None, :, 1] - 54) - preset['temperature_drop_c'] * field
    soil = np.empty_like(rainfall)
    soil[0] = config['initial_soil_moisture']
    for i in range(1, len(h)):
        # Illustrative shallow-soil bucket: rain infiltration and drainage toward initial wetness.
        soil[i] = np.clip(soil[i-1] + rainfall[i] * .0015
                          - .015 * (soil[i-1] - config['initial_soil_moisture']), .05, .55)
    return {
        'config': config, 'hours': h, 'timestamps': timestamps, 'centres': track(config, h),
        'variables': {'wind_gusts_10m': gust, 'wind_speed_10m': wind,
                      'wind_direction_10m': direction, 'precipitation': rainfall,
                      'temperature_2m': temperature, 'soil_moisture_0_to_7cm': soil},
    }
