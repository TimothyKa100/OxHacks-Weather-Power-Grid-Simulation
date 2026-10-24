// Turns the backend's discrete playback frames into a continuous timeline.
// Time t is in storm hours: hour h's weather window spans (h - 1, h], and the
// intact network is shown at t = 0. Weather trips land partway into their
// hour; each simultaneous overload round follows a moment later. (Cascades are
// instantaneous in the model; the small stagger only makes them readable.)

const WEATHER_AT = 0.3;
const CASCADE_GAP = 0.12;
export const TAIL = 0.4; // let the storm drift off after the last window
const SHED_EASE = 0.25; // hours over which shed MW counts up to a new value
const FADE = 0.1; // hours over which a line or bus changes colour (below CASCADE_GAP)
const FLASH = 0.35; // hours a trip flash lasts

export function buildTimeline(frames) {
  const seen = new Map();
  const times = frames.map(f => {
    if (f.hour === 0) return 0;
    const k = seen.get(f.hour) || 0;
    seen.set(f.hour, k + 1);
    const inHour = frames.filter(g => g.hour === f.hour).length;
    const gap = Math.min(CASCADE_GAP, (0.95 - WEATHER_AT) / Math.max(1, inHour));
    return f.hour - 1 + WEATHER_AT + k * gap;
  });
  const lastHour = frames[frames.length - 1].hour;
  return {frames, times, lastHour, end: lastHour + TAIL};
}

// The state shown at t, the state before it, and how far (0-1) the change
// from one to the other has faded in. Works for failures and for restorations.
export function stateAt(timeline, t) {
  const i = frameIndexAt(timeline, t);
  const frame = timeline.frames[i];
  const previous = i > 0 ? timeline.frames[i - 1] : frame;
  const fade = i > 0 ? Math.min(1, (t - timeline.times[i]) / FADE) : 1;
  return {i, frame, previous, fade};
}

// Branches that tripped within the last FLASH hours, with flash progress 0-1.
export function flashesAt(timeline, t) {
  const flashes = new Map();
  for (let i = frameIndexAt(timeline, t); i > 0 && t - timeline.times[i] < FLASH; i--) {
    const before = timeline.frames[i - 1].status;
    timeline.frames[i].status.forEach((s, b) => {
      if (s !== 'online' && before[b] === 'online' && !flashes.has(b)) {
        flashes.set(b, {progress: (t - timeline.times[i]) / FLASH, status: s});
      }
    });
  }
  return flashes;
}

// Blend two #rrggbb colours.
export function mix(a, b, u) {
  const pa = [1, 3, 5].map(k => parseInt(a.slice(k, k + 2), 16));
  const pb = [1, 3, 5].map(k => parseInt(b.slice(k, k + 2), 16));
  return `#${pa.map((v, k) => Math.round(v + (pb[k] - v) * u).toString(16).padStart(2, '0')).join('')}`;
}

export function frameIndexAt(timeline, t) {
  let index = 0;
  for (let i = 0; i < timeline.times.length; i++) if (timeline.times[i] <= t) index = i;
  return index;
}

export function shedAt(timeline, t) {
  const i = frameIndexAt(timeline, t);
  const current = timeline.frames[i].shed_mw;
  if (i === 0) return current;
  const previous = timeline.frames[i - 1].shed_mw;
  const f = Math.min(1, (t - timeline.times[i]) / SHED_EASE);
  return previous + (current - previous) * f;
}
