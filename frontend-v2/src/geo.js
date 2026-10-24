import outline from './uk-outline.json';

export {outline};

const {bounds, cos_lat: cosLat, scale} = outline;

// Same equirectangular projection the outline was generated with.
export function project(lon, lat) {
  return [(lon - bounds.lon_min) * cosLat * scale, (bounds.lat_max - lat) * scale];
}

// Until the backend provides real substation locations, schematic 0–1
// coordinates are stretched over England and Wales. Clearly not geographic.
const PLACEHOLDER = {lonWest: -3.2, lonEast: 0.6, latNorth: 54.5, latSouth: 51.0};

function placeholder(x, y) {
  return project(
    PLACEHOLDER.lonWest + x * (PLACEHOLDER.lonEast - PLACEHOLDER.lonWest),
    PLACEHOLDER.latNorth - y * (PLACEHOLDER.latNorth - PLACEHOLDER.latSouth),
  );
}

const hasLatLon = p => Number.isFinite(p?.lat) && Number.isFinite(p?.lon);

export function isGeographic(buses) {
  return buses.every(hasLatLon);
}

// Positions for every bus: all real coordinates, or all placeholders, never a mix.
export function busPositions(buses) {
  const geographic = isGeographic(buses);
  return buses.map(b => (geographic ? project(b.lon, b.lat) : placeholder(b.x, b.y)));
}

// Storm centre for a weather window, in the same frame as the buses.
export function stormPosition(w, geographic) {
  if (!w) return null;
  if (hasLatLon(w)) return project(w.lon, w.lat);
  if (!geographic && Number.isFinite(w.center_x) && Number.isFinite(w.center_y)) {
    return placeholder(w.center_x, w.center_y);
  }
  return null;
}

const smoothstep = (a, b, x) => {
  const u = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return u * u * (3 - 2 * u);
};

// Continuous storm state at time t (storm hours). Each window's centre is
// placed at the middle of its hour and joined with a Catmull-Rom curve, so the
// storm glides instead of jumping. Before the first and after the last window
// it keeps moving along the end segments while fading in or out.
export function stormAt(windows, t, geographic, lastHour, tail) {
  const keys = (windows || [])
    .map(w => ({time: w.hour - 0.5, p: stormPosition(w, geographic), strength: Number.isFinite(w.strength) ? w.strength : 1}))
    .filter(k => k.p)
    .sort((a, b) => a.time - b.time);
  if (!keys.length) return null;

  const alpha = smoothstep(0, 0.6, t) * (1 - smoothstep(lastHour, lastHour + tail, t));
  if (alpha <= 0.001) return null;
  if (keys.length === 1) return {x: keys[0].p[0], y: keys[0].p[1], strength: keys[0].strength, alpha};

  const last = keys.length - 1;
  const lerp = (a, b, u) => a + (b - a) * u;
  if (t <= keys[0].time || t >= keys[last].time) {
    const [a, b] = t <= keys[0].time ? [keys[0], keys[1]] : [keys[last - 1], keys[last]];
    const u = (t - a.time) / (b.time - a.time);
    const end = t <= keys[0].time ? keys[0] : keys[last];
    return {x: lerp(a.p[0], b.p[0], u), y: lerp(a.p[1], b.p[1], u), strength: end.strength, alpha};
  }

  let i = 0;
  while (i < last - 1 && t > keys[i + 1].time) i++;
  const u = (t - keys[i].time) / (keys[i + 1].time - keys[i].time);
  const p1 = keys[i].p;
  const p2 = keys[i + 1].p;
  const p0 = i > 0 ? keys[i - 1].p : [2 * p1[0] - p2[0], 2 * p1[1] - p2[1]];
  const p3 = i + 2 <= last ? keys[i + 2].p : [2 * p2[0] - p1[0], 2 * p2[1] - p1[1]];
  const cr = (a, b, c, d) =>
    0.5 * (2 * b + (-a + c) * u + (2 * a - 5 * b + 4 * c - d) * u * u + (-a + 3 * b - 3 * c + d) * u * u * u);
  return {
    x: cr(p0[0], p1[0], p2[0], p3[0]),
    y: cr(p0[1], p1[1], p2[1], p3[1]),
    strength: lerp(keys[i].strength, keys[i + 1].strength, u),
    alpha,
  };
}
