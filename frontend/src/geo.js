import outline from './uk-outline.json';

export {outline};

const {bounds, cos_lat: cosLat, scale} = outline;

// Same equirectangular projection the outline was generated with.
export function project(lon, lat) {
  return [(lon - bounds.lon_min) * cosLat * scale, (bounds.lat_max - lat) * scale];
}

export function unproject(x, y) {
  return [x / (cosLat * scale) + bounds.lon_min, bounds.lat_max - y / scale];
}

const hasLatLon = p => Number.isFinite(p?.lat) && Number.isFinite(p?.lon);

export function isGeographic(buses) {
  return buses.every(hasLatLon);
}

// Least-squares fit of v = a*u + b.
function fitLine(us, vs) {
  const n = us.length;
  const mu = us.reduce((s, u) => s + u, 0) / n;
  const mv = vs.reduce((s, v) => s + v, 0) / n;
  let suv = 0;
  let suu = 0;
  for (let i = 0; i < n; i++) {
    suv += (us[i] - mu) * (vs[i] - mv);
    suu += (us[i] - mu) ** 2;
  }
  const a = suu > 0 ? suv / suu : 0;
  return {a, b: mv - a * mu};
}

// Until a network has real substation locations, its schematic 0–1 coordinates
// are stretched over England and Wales. Clearly not geographic.
const PLACEHOLDER = {lonWest: -3.2, lonEast: 0.6, latNorth: 54.5, latSouth: 51.0};

// Everything the backend sends (bus x/y, route geometry, storm centre, pylon
// positions, outages, weather cells) is in the network's map units. The backend
// builds those from lon/lat with a separate linear scale per axis, so the buses'
// own lat/lon recover it exactly; then everything lands on the UK outline.
export function mapFrame(buses) {
  const geographic = isGeographic(buses);
  let toLonLat;
  let degLatPerUnit;
  if (geographic) {
    const lon = fitLine(buses.map(b => b.x), buses.map(b => b.lon));
    const lat = fitLine(buses.map(b => b.y), buses.map(b => b.lat));
    toLonLat = (x, y) => [lon.a * x + lon.b, lat.a * y + lat.b];
    degLatPerUnit = Math.abs(lat.a);
  } else {
    const {lonWest, lonEast, latNorth, latSouth} = PLACEHOLDER;
    toLonLat = (x, y) => [lonWest + x * (lonEast - lonWest), latNorth - y * (latNorth - latSouth)];
    degLatPerUnit = latNorth - latSouth;
  }
  return {
    geographic,
    px: (x, y) => project(...toLonLat(x, y)),
    // Map units are isotropic (x is scaled by cos(latitude)), so one factor converts lengths.
    pxPerUnit: degLatPerUnit * scale,
  };
}
