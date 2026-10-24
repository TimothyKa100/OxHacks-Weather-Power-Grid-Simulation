// Checks the parts of a /api/storm response the map draws, so a changed backend
// format produces a clear message instead of a blank or broken map.

const isNum = v => typeof v === 'number' && Number.isFinite(v);
const isPoint = p => Array.isArray(p) && isNum(p[0]) && isNum(p[1]);

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
    if (e.geometry !== undefined && !(Array.isArray(e.geometry) && e.geometry.every(isPoint))) {
      return `Branch ${e.id} has an invalid route geometry`;
    }
  }
  return null;
}

export function checkRun(run) {
  const problem = checkNetwork(run?.network);
  if (problem) return problem;
  const frames = run.frames;
  if (!Array.isArray(frames) || !frames.length) return 'frames is missing';
  const n = run.network.buses.length;
  const m = run.network.branches.length;
  for (const [i, f] of frames.entries()) {
    if (!isNum(f.hour)) return `frames[${i}].hour must be a number`;
    if (!Array.isArray(f.status) || f.status.length !== m) return `frames[${i}].status needs ${m} entries`;
    if (!Array.isArray(f.served_by_bus) || f.served_by_bus.length !== n) return `frames[${i}].served_by_bus needs ${n} entries`;
    if (!Array.isArray(f.events)) return `frames[${i}].events must be an array`;
    if (f.storm != null && !isPoint(f.storm)) return `frames[${i}].storm must be an [x, y] point`;
  }
  // Real storms: measured gusts and the real outages to compare against.
  if (run.storm) {
    const w = run.weather;
    if (!w || !Array.isArray(w.nodes) || !w.nodes.every(isPoint) || !Array.isArray(w.gust_kmh)) {
      return 'weather.nodes and weather.gust_kmh are required for a real storm';
    }
    if (w.gust_kmh.some(row => !Array.isArray(row) || row.length !== w.nodes.length)) {
      return `weather.gust_kmh rows need ${w.nodes.length} entries`;
    }
    if (!Array.isArray(run.outages) || !run.outages.every(o => isPoint(o) && isNum(o[2]))) {
      return 'outages must be [x, y, hour] entries';
    }
    if (!run.benchmark || !Array.isArray(run.benchmark.observed_per_hour) || !Array.isArray(run.benchmark.modelled_per_hour)) {
      return 'benchmark.observed_per_hour and benchmark.modelled_per_hour are required';
    }
  }
  return null;
}
