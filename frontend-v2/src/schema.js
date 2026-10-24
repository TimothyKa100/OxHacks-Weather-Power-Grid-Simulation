// Checks the parts of the API response the map draws, so a changed backend
// format produces a clear message instead of a blank or broken map.

const isNum = v => typeof v === 'number' && Number.isFinite(v);

export function checkNetwork(net) {
  if (!net || !Array.isArray(net.buses) || !Array.isArray(net.branches)) {
    return 'network.buses and network.branches must be arrays';
  }
  const ids = new Set(net.buses.map(b => b.id));
  // Either every bus has lat/lon, or every bus has schematic x/y (see geo.js).
  const allLatLon = net.buses.every(b => isNum(b.lat) && isNum(b.lon));
  const missingXY = net.buses.find(b => !(isNum(b.x) && isNum(b.y)));
  if (!allLatLon && missingXY) return `Bus ${missingXY.id} needs lat/lon or x/y coordinates`;
  for (const b of net.buses) {
    if (!isNum(b.load_mw)) return `Bus ${b.id} needs a numeric load_mw`;
  }
  for (const e of net.branches) {
    if (!ids.has(e.from) || !ids.has(e.to)) return `Branch ${e.id} connects unknown buses`;
  }
  return null;
}

// The storm replay for the unmitigated grid ("no action" portfolio).
export function checkFrames(result) {
  const problem = checkNetwork(result?.network);
  if (problem) return problem;
  const frames = result.portfolios?.none?.playback?.frames;
  if (!Array.isArray(frames) || !frames.length) return 'portfolios.none.playback.frames is missing';
  const n = result.network.buses.length;
  const m = result.network.branches.length;
  for (const [i, f] of frames.entries()) {
    if (!Array.isArray(f.status) || f.status.length !== m) return `frames[${i}].status needs ${m} entries`;
    if (!Array.isArray(f.served_by_bus) || f.served_by_bus.length !== n) return `frames[${i}].served_by_bus needs ${n} entries`;
    if (!isNum(f.hour) || !isNum(f.shed_mw)) return `frames[${i}] needs numeric hour and shed_mw`;
  }
  return null;
}
