"""Optional ML feature adapter for synthetic weather; requires requirements-model.txt.

Never invokes historical-only inference or changes evidence/training gates.
"""
import pandas as pd

from backend.storm_scenarios import generate_weather, validate_scenario
from . import FEATURE_VERSION


def scenario_features(builder, storm_config):
    """Return the existing ordered feature contract for each visible cutoff hour.

    builder is a FeatureBuilder using existing assets, route fractions and bearings.
    Weather download availability is irrelevant to a fully synthetic scenario;
    historical builder state is copied before enabling synthetic route coverage.
    Missing static asset properties remain missing rather than being invented.
    """
    import copy
    config = validate_scenario(storm_config)
    keys = sorted(builder.links.weather_key.unique())
    locations = []
    for key in keys:
        lat, lon = map(float, key.split('_'))
        locations.append([lon, lat])
    generated = generate_weather(config, locations)
    index = pd.DatetimeIndex(generated['timestamps'])
    weather = {key: pd.DataFrame({name: values[:, i] for name, values in generated['variables'].items()}, index=index)
               for i, key in enumerate(keys)}
    synthetic = copy.copy(builder)
    synthetic.assets = builder.assets.copy()
    synthetic.assets['route_weather_complete'] = synthetic.assets.asset_id.isin(builder.groups)
    start = pd.Timestamp(config['start_time'])
    # Cutoffs [start, start+hours) have all 72 prior hours; no weather at/after
    # a cutoff leaks into its instantaneous or accumulated exposure predictors.
    keep = index < start + pd.Timedelta(hours=config['hours'])
    table = synthetic.batch_features(index[keep], {key: frame.loc[keep] for key, frame in weather.items()}, start)
    metadata = {**(table.schema.metadata or {}), b'mode': b'synthetic_scenario',
                b'feature_version': FEATURE_VERSION.encode(), b'weather_source': b'synthetic-storm-v1'}
    return table.replace_schema_metadata(metadata)
