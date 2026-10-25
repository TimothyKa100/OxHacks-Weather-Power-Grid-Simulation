// Turns the backend's hourly frames into a continuous timeline.
// Time t is in storm hours: frame h holds the settled state after hour h, whose
// weather spans (h - 1, h]. Its failures land mid-hour; the intact network is
// shown at t = 0. Everything the map draws is a function of t.

const EVENT_AT = 0.5;
export const TAIL = 0.4; // let the storm drift off after the last hour
const FADE = 0.12; // hours over which a line or bus changes colour
const FLASH = 0.5; // hours a failure flash lasts
const GOLDEN = 0.6180339887;
// Heavy map layers (outage dots, gust field) redraw on this time step, not every tick.
export const LAYER_STEP = 0.05;
export const layerTime = t => Math.round(t / LAYER_STEP) * LAYER_STEP;

// Protection rounds of a cascade are spread over this many hours each after the hour's failures,
// and playback slows down there (App.jsx) so every round can be seen.
export const ROUND_GAP = 0.06;
const CASCADE_HOLD = 0.12; // the settled state stays in focus this long after the last round

export function buildTimeline(run) {
  const {frames} = run;
  const times = frames.map(f => (f.hour === 0 ? 0 : f.hour - 1 + EVENT_AT + (f.cascade ? (f.cascade.round - 1) * ROUND_GAP : 0)));
  const lastHour = frames[frames.length - 1].hour;
  // One window per hour with overload trips: when it starts and ends, and which branches tripped.
  const cascades = [];
  frames.forEach((f, i) => {
    if (!f.cascade || f.cascade.settled) return;
    let w = cascades[cascades.length - 1];
    if (!w || w.hour !== f.hour) cascades.push(w = {hour: f.hour, start: times[i], end: times[i], rounds: f.cascade.rounds, tripped: []});
    w.tripped.push(...(f.cascade.tripped || []));
    w.end = times[i] + ROUND_GAP + CASCADE_HOLD;
  });

  // Synthetic storm centre per hour, sized by how strong that hour's gusts are.
  const peakGust = Math.max(1, ...frames.map(f => Math.max(0, ...(f.gust_kmh || [0]))));
  const stormKeys = frames
    .filter(f => f.hour > 0 && Array.isArray(f.storm))
    .map(f => ({time: f.hour - 0.5, p: f.storm, radius: f.storm_radius || 0.1, strength: Math.max(0, ...(f.gust_kmh || [0])) / peakGust}));

  // Real outages start somewhere inside their recorded hour; spread them evenly
  // (deterministically) so they appear continuously rather than in hourly bursts.
  const outageTimes = (run.outages || []).map((o, i) => o[2] + ((i * GOLDEN) % 1));

  return {frames, times, lastHour, end: lastHour + TAIL, stormKeys, outageTimes, cascades};
}

// The cascade window containing t (with a short lead-in for the camera), if any.
export function cascadeAt(timeline, t, lead = 0) {
  return timeline.cascades.find(w => t >= w.start - lead && t <= w.end) || null;
}

export function frameIndexAt(timeline, t) {
  let index = 0;
  for (let i = 0; i < timeline.times.length; i++) if (timeline.times[i] <= t) index = i;
  return index;
}

// The state shown at t, the state before it, and how far (0-1) the change
// between them has faded in. Works for failures and for reclosed lines.
export function stateAt(timeline, t) {
  const i = frameIndexAt(timeline, t);
  const frame = timeline.frames[i];
  const previous = i > 0 ? timeline.frames[i - 1] : frame;
  const fade = i > 0 ? Math.min(1, (t - timeline.times[i]) / FADE) : 1;
  return {i, frame, previous, fade};
}

// Branches that went down within the last FLASH hours, with flash progress 0-1.
export function flashesAt(timeline, t) {
  const flashes = [];
  const seen = new Set();
  for (let i = frameIndexAt(timeline, t); i > 0 && t - timeline.times[i] < FLASH; i--) {
    const before = timeline.frames[i - 1].status;
    const frame = timeline.frames[i];
    frame.status.forEach((s, b) => {
      if (s !== 'online' && before[b] === 'online' && !seen.has(b)) {
        seen.add(b);
        flashes.push({branch: b, status: s, at: frame.fallen_at?.[b] || null, progress: (t - timeline.times[i]) / FLASH});
      }
    });
  }
  return flashes;
}

const smoothstep = (a, b, x) => {
  const u = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return u * u * (3 - 2 * u);
};

// Synthetic storm centre at t: hourly centres joined by a Catmull-Rom curve so the
// storm glides; it fades in at the start and out after the last hour.
export function stormAt(timeline, t) {
  const keys = timeline.stormKeys;
  if (!keys.length) return null;
  const alpha = smoothstep(0, 0.8, t) * (1 - smoothstep(timeline.lastHour, timeline.end, t));
  if (alpha <= 0.001) return null;
  const last = keys.length - 1;
  const lerp = (a, b, u) => a + (b - a) * u;
  if (keys.length === 1 || t <= keys[0].time || t >= keys[last].time) {
    const k = keys.length === 1 || t <= keys[0].time ? keys[0] : keys[last];
    return {x: k.p[0], y: k.p[1], radius: k.radius, strength: k.strength, alpha};
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
    radius: lerp(keys[i].radius, keys[i + 1].radius, u),
    strength: lerp(keys[i].strength, keys[i + 1].strength, u),
    alpha,
  };
}

// Real storm: measured gust at every weather node at t, interpolated between the
// hourly values (each centred mid-hour). Null where the weather model has no data.
export function gustsAt(weather, t) {
  const rows = weather.gust_kmh;
  const u = Math.min(rows.length - 1, Math.max(0, t - 0.5));
  const k = Math.min(rows.length - 2, Math.floor(u));
  if (k < 0) return rows[0];
  const f = u - k;
  return rows[k].map((g, n) => {
    const h = rows[k + 1][n];
    if (g == null || h == null) return g ?? h;
    return g + (h - g) * f;
  });
}

// Blend two #rrggbb colours.
export function mix(a, b, u) {
  const pa = [1, 3, 5].map(k => parseInt(a.slice(k, k + 2), 16));
  const pb = [1, 3, 5].map(k => parseInt(b.slice(k, k + 2), 16));
  return `#${pa.map((v, k) => Math.round(v + (pb[k] - v) * u).toString(16).padStart(2, '0')).join('')}`;
}
