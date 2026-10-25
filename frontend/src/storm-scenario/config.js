// Framework/map independent v1 contract. Coordinates are WGS84 lon/lat.
// Keep this folder portable: only the React components/hooks import React.
export const STORM_PRESETS = Object.freeze({
  single_peak: 'Windstorm', persistent: 'Prolonged wind and rain', double_peak: 'Two successive fronts',
});
export const DEFAULT_STORM = Object.freeze({
  version: 1, origin: Object.freeze({lon: -10, lat: 50.5}), bearing_deg: 45,
  speed_kmh: 35, radius_km: 150, peak_gust_kmh: 160, hours: 36,
  preset: 'single_peak', seed: 1, start_time: '2026-01-01T00:00:00+00:00', initial_soil_moisture: .3,
});
export const STORM_LIMITS = Object.freeze({
  bearing_deg: [0, 360], speed_kmh: [5, 100], radius_km: [30, 400],
  peak_gust_kmh: [60, 250], hours: [6, 72], seed: [0, 2147483647], initial_soil_moisture: [.05, .55],
});
export const newStorm = () => ({...DEFAULT_STORM, origin: {...DEFAULT_STORM.origin}});
const radians = degrees => degrees * Math.PI / 180;

export function stormPoint(config, hour) {
  const d = hour * config.speed_kmh;
  return [config.origin.lon + d * Math.sin(radians(config.bearing_deg)) / (111.32 * Math.cos(radians(config.origin.lat))),
    config.origin.lat + d * Math.cos(radians(config.bearing_deg)) / 111.32];
}

export function directionTo(config, [lon, lat]) {
  const east = (lon - config.origin.lon) * 111.32 * Math.cos(radians(config.origin.lat));
  const north = (lat - config.origin.lat) * 111.32;
  if (Math.hypot(east, north) < 1) return config;
  return {...config, bearing_deg: Math.round((Math.atan2(east, north) * 180 / Math.PI + 360) % 360)};
}

export function checkStorm(config) {
  if (!config || config.version !== 1) return 'Unsupported storm configuration version.';
  if (!Object.hasOwn(STORM_PRESETS, config.preset)) return 'Choose a known storm preset.';
  for (const [key, [min, max]] of Object.entries(STORM_LIMITS)) {
    if (typeof config[key] !== 'number' || !Number.isFinite(config[key]) || config[key] < min || config[key] > max) {
      return `${key.replaceAll('_', ' ')} must be between ${min} and ${max}.`;
    }
  }
  if (!Number.isInteger(config.hours) || !Number.isInteger(config.seed)) return 'Hours and seed must be integers.';
  if (!Number.isFinite(config.origin?.lon) || config.origin.lon < -25 || config.origin.lon > 15
      || !Number.isFinite(config.origin?.lat) || config.origin.lat < 45 || config.origin.lat > 65) {
    return 'Choose a starting point between 25°W–15°E and 45–65°N.';
  }
  const date = new Date(config.start_time);
  if (!/(Z|[+-]\d\d:\d\d)$/.test(config.start_time) || !Number.isFinite(date.getTime())
      || date.getUTCMinutes() || date.getUTCSeconds() || date.getUTCMilliseconds()) return 'Choose an aligned start hour with a timezone.';
  return null;
}
