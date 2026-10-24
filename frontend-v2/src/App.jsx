import React, {useEffect, useState} from 'react';
import {OfflineError, fetchNetwork, loadSample, simulate} from './api.js';
import {checkFrames, checkNetwork} from './schema.js';
import {isGeographic} from './geo.js';
import {buildTimeline, frameIndexAt, shedAt} from './timeline.js';
import UkMap from './UkMap.jsx';

const MS_PER_HOUR = 1800;

export default function App() {
  const [network, setNetwork] = useState(null);
  const [run, setRun] = useState(null); // {timeline, weather}
  const [t, setT] = useState(0); // storm hours since the start of the replay
  const [busy, setBusy] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState('');

  // Draw the intact grid on load.
  useEffect(() => {
    fetchNetwork()
      .catch(async err => {
        if (!(err instanceof OfflineError)) throw err;
        setOffline(true);
        return (await loadSample()).network;
      })
      .then(net => {
        const problem = checkNetwork(net);
        if (problem) setError(`Unexpected network data: ${problem}`);
        else setNetwork(net);
      })
      .catch(err => setError(err.message));
  }, []);

  // Advance a continuous clock (~60 updates/s) until the storm has passed.
  // Time comes from performance.now(), so motion stays smooth even if ticks
  // are delayed. A timer is used instead of requestAnimationFrame, which some
  // embedded or occluded views never fire.
  const playing = Boolean(run) && t < run.timeline.end;
  useEffect(() => {
    if (!run) return undefined;
    const started = performance.now();
    const id = setInterval(() => {
      const next = Math.min(run.timeline.end, (performance.now() - started) / MS_PER_HOUR);
      setT(next);
      if (next >= run.timeline.end) clearInterval(id);
    }, 1000 / 60);
    return () => clearInterval(id);
  }, [run]);

  async function onSimulate() {
    setBusy(true);
    setError('');
    try {
      let result;
      try {
        result = await simulate();
        setOffline(false);
      } catch (err) {
        if (!(err instanceof OfflineError)) throw err;
        setOffline(true);
        result = await loadSample();
      }
      const problem = checkFrames(result);
      if (problem) throw new Error(`Unexpected simulation data: ${problem}`);
      setNetwork(result.network);
      setT(0);
      setRun({timeline: buildTimeline(result.portfolios.none.playback.frames), weather: result.weather});
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const timeline = run?.timeline;
  let status = 'Press Simulate to send a storm across the grid.';
  let announcement = '';
  if (timeline) {
    const frame = timeline.frames[frameIndexAt(timeline, t)];
    const down = frame.status.filter(s => s !== 'online').length;
    const hour = Math.min(timeline.lastHour, Math.max(0, Math.ceil(t)));
    const lines = `${down} ${down === 1 ? 'line' : 'lines'} down`;
    status = `Hour ${hour} of ${timeline.lastHour} · ${lines} · ${shedAt(timeline, t).toFixed(0)} MW without power`;
    // Screen readers hear each recorded state once, not every animation frame.
    announcement = `Hour ${frame.hour}: ${lines}, ${frame.shed_mw.toFixed(0)} megawatts without power`;
  }

  return (
    <main className="page">
      <div className="map-wrap">
        {network ? (
          <UkMap network={network} timeline={timeline} t={t} weather={run?.weather} />
        ) : (
          !error && <p className="note">Loading grid…</p>
        )}
      </div>

      <div className="panel">
        <button type="button" className="simulate" onClick={onSimulate} disabled={busy || playing || !network}>
          {busy ? 'Simulating…' : playing ? 'Storm in progress…' : 'Simulate'}
        </button>
        <div className="progress" aria-hidden="true">
          <i style={{width: timeline ? `${(100 * t) / timeline.end}%` : 0}} />
        </div>
        <p className="status">{status}</p>
        <p className="visually-hidden" aria-live="polite">
          {announcement}
        </p>
        {error && (
          <p className="note error" role="alert">
            {error}
          </p>
        )}
        {offline && <p className="note">Backend offline: using the bundled sample data.</p>}
        {network && !isGeographic(network.buses) && (
          <p className="note">Placeholder positions: this test grid has no real UK locations yet.</p>
        )}
      </div>
    </main>
  );
}
