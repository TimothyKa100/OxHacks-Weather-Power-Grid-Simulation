import React, {useEffect, useMemo, useState} from 'react';
import {OfflineError, fetchStorms, loadSample, simulate} from './api.js';
import {checkRun} from './schema.js';
import {buildTimeline, frameIndexAt, layerTime} from './timeline.js';
import UkMap from './UkMap.jsx';
import StormChart from './StormChart.jsx';

const PLAYBACK_MS = 22000; // a whole storm plays in about this long
const TICK_MS = 1000 / 30;
const SYNTHETIC = 'synthetic';

const month = iso => new Date(iso).toLocaleDateString('en-GB', {month: 'short', year: 'numeric', timeZone: 'UTC'});
const clock = iso =>
  new Date(iso).toLocaleString('en-GB', {weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC'});
const plural = (n, word) => `${n.toLocaleString('en-GB')} ${word}${n === 1 ? '' : 's'}`;

export default function App() {
  const [storms, setStorms] = useState([]);
  const [scenario, setScenario] = useState(SYNTHETIC);
  const [run, setRun] = useState(null);
  const [network, setNetwork] = useState(null);
  const [t, setT] = useState(0); // storm hours since the start of the replay
  const [busy, setBusy] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState('');

  // Storm list for the picker, and the intact grid to draw before the first run.
  useEffect(() => {
    fetchStorms()
      .then(list => {
        setStorms(list.storms || []);
        if (list.network) setNetwork(list.network);
      })
      .catch(async err => {
        if (!(err instanceof OfflineError)) return setError(err.message);
        setOffline(true);
        setNetwork((await loadSample()).network);
      });
  }, []);

  const timeline = useMemo(() => (run ? buildTimeline(run) : null), [run]);

  // Advance a continuous clock until the storm has passed. Time comes from
  // performance.now(), so motion stays smooth even if ticks are delayed. A timer
  // is used rather than requestAnimationFrame, which some embedded views never fire.
  const playing = Boolean(timeline) && t < timeline.end;
  useEffect(() => {
    if (!timeline) return undefined;
    const msPerHour = Math.min(1800, Math.max(250, PLAYBACK_MS / timeline.end));
    const started = performance.now();
    const id = setInterval(() => {
      const next = Math.min(timeline.end, (performance.now() - started) / msPerHour);
      setT(next);
      if (next >= timeline.end) clearInterval(id);
    }, TICK_MS);
    return () => clearInterval(id);
  }, [timeline]);

  async function onSimulate() {
    setBusy(true);
    setError('');
    try {
      let result;
      try {
        result = await simulate(scenario);
        setOffline(false);
      } catch (err) {
        if (!(err instanceof OfflineError)) throw err;
        setOffline(true);
        if (scenario !== SYNTHETIC) throw new Error('Real storms need the backend running.');
        result = await loadSample();
      }
      const problem = checkRun(result);
      if (problem) throw new Error(`Unexpected simulation data: ${problem}`);
      setNetwork(result.network);
      setT(0);
      setRun(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  let status = 'Pick a storm and press Simulate.';
  let announcement = '';
  if (timeline) {
    const frame = timeline.frames[frameIndexAt(timeline, t)];
    const pylons = frame.status.filter(s => s === 'pylon').length;
    const tripped = frame.status.filter(s => s === 'overload').length;
    const parts = [plural(pylons, 'pylon') + ' down', plural(tripped, 'line') + ' tripped'];
    let when = `Hour ${Math.min(timeline.lastHour, Math.max(0, Math.ceil(t)))} of ${timeline.lastHour}`;
    if (run.storm) {
      const hour = Math.min(run.storm.timestamps.length - 1, Math.max(0, Math.floor(t)));
      when = `${clock(run.storm.timestamps[hour])} UTC`;
      // Same time step as the dots on the map, so the count always matches them.
      parts.push(plural(timeline.outageTimes.filter(x => x <= layerTime(t)).length, 'real power cut'));
    }
    status = [when, ...parts].join(' · ');
    // Screen readers hear each hourly state once, not every animation tick.
    announcement = `Hour ${frame.hour}: ${parts.slice(0, 2).join(', ')}`;
  }

  return (
    <main className="page">
      {/* Fixed bands: the map keeps the same size for every storm. The top band holds the
          real-storm comparison (empty for synthetic storms) and any notices. */}
      <section className="top-band" aria-label="Real storm comparison">
        {run?.storm && timeline && (
          <>
            <StormChart benchmark={run.benchmark} timestamps={run.storm.timestamps} t={t} />
            <Comparison run={run} />
          </>
        )}
        {error && (
          <p className="note error" role="alert">
            {error}
          </p>
        )}
        {offline && <p className="note">Backend offline: using a bundled sample storm.</p>}
      </section>

      <div className="map-wrap">
        {network ? <UkMap network={network} run={run} timeline={timeline} t={t} /> : !error && <p className="note">Loading grid…</p>}
      </div>

      <div className="panel">
        <div className="controls">
          <label className="visually-hidden" htmlFor="storm">
            Storm
          </label>
          <select id="storm" value={scenario} onChange={e => setScenario(e.target.value)} disabled={busy || playing}>
            <option value={SYNTHETIC}>Synthetic storm</option>
            {storms.map(s => (
              <option key={s.id} value={s.id}>
                {s.name} · {month(s.start)}
              </option>
            ))}
          </select>
          <button type="button" className="simulate" onClick={onSimulate} disabled={busy || playing || !network}>
            {busy ? 'Simulating…' : playing ? 'Playing…' : 'Simulate'}
          </button>
        </div>
        <div className="progress" aria-hidden="true">
          <i style={{width: timeline ? `${(100 * t) / timeline.end}%` : 0}} />
        </div>
        <p className="status">{status}</p>
        <p className="visually-hidden" aria-live="polite">
          {announcement}
        </p>
        <p className="credit">
          Grid © OpenStreetMap contributors (ODbL) via uk-grid-atlas
          {run?.storm && <> · Gusts: Met Office UKV via Open-Meteo.com (CC BY 4.0), © Crown copyright</>}
        </p>
      </div>
    </main>
  );
}

// Short verdict under the chart: does the modelled risk land in the right hours and places?
function Comparison({run}) {
  const b = run.benchmark;
  const s = run.storm;
  const fixed = (v, d) => (Number.isFinite(v) ? v.toFixed(d) : '–');
  const hourLabel = i => (s.timestamps[i] ? clock(s.timestamps[i]) : `hour ${i}`);
  return (
    <p className="note comparison">
      Peaks: actual {hourLabel(b.peak_hour_observed)}, predicted {hourLabel(b.peak_hour_modelled)} UTC · timing r ={' '}
      {fixed(b.timing_correlation, 2)} · windiest areas {fixed(b.rise_windiest_third, 1)}× normal outages, calmest{' '}
      {fixed(b.rise_calmest_third, 1)}× (ρ = {fixed(b.place_rank_correlation, 2)}). Dots are {s.operator} distribution
      faults; shaded areas have outage data.
    </p>
  );
}
