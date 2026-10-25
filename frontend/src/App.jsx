import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {OfflineError, fetchRiskLayer, fetchRiskLayers, fetchStorms, fetchViewNetwork, loadSample, simulate} from './api.js';
import {checkRun, checkNetwork} from './schema.js';
import {buildTimeline, frameIndexAt, layerTime} from './timeline.js';
import UkMap from './UkMap.jsx';
import StormChart from './StormChart.jsx';
import DecisionPanel from './DecisionPanel.jsx';
import {portfolioRun, sameStormRequest, portfolioNames} from './planning.js';

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
  const [config,setConfig]=useState({seed:1,peak_gust:160,hours:36,storm_type:'single_peak',samples:16,budget:5,crews:6});
  const [policy,setPolicy]=useState('none'),[draft,setDraft]=useState([]),[asset,setAsset]=useState('');
  const [economics,setEconomics]=useState({cost_per_unit_gbp:10000,outage_value_gbp_per_mwh:1000,expected_events:1,asset_costs_gbp:{}});
  const [playing,setPlaying]=useState(false);
  const [importedWeather,setImportedWeather]=useState(null);
  // Corridor outage-risk layer: drawn beside the engineering line model, never simulated.
  const [riskLayers,setRiskLayers]=useState([]);
  const [riskId,setRiskId]=useState('');
  const [riskLayer,setRiskLayer]=useState(null);
  const shown=useMemo(()=>portfolioRun(run,policy),[run,policy]);
  // The simulated network follows the view: before a run it is rebuilt for whatever is on screen;
  // a finished run keeps the network it was simulated on until you rebuild for the new view.
  const [viewBox,setViewBox]=useState(null);
  const [runView,setRunView]=useState(null);
  const viewKey=viewBox?viewBox.map(v=>v.toFixed(3)).join(','):'';
  const onView=useCallback(box=>setViewBox(previous=>previous&&previous.every((v,i)=>Math.abs(v-box[i])<1e-6)?previous:box),[]);
  useEffect(()=>{
    if(!viewBox||run||offline)return undefined;
    const controller=new AbortController();
    const timer=setTimeout(()=>{fetchViewNetwork(viewBox,controller.signal).then(net=>{if(net&&!net.error)setNetwork(net)}).catch(()=>{})},250);
    return ()=>{clearTimeout(timer);controller.abort()};
  },[viewKey,run,offline]);  // eslint-disable-line react-hooks/exhaustive-deps
  const viewChanged=run&&runView&&viewKey&&runView!==viewKey;
  // The map frame is exactly as wide as the storm picker and Simulate button.
  const controlsRef=useRef(null);
  const [mapWidth,setMapWidth]=useState(null);
  useEffect(()=>{
    const el=controlsRef.current;
    if(!el)return undefined;
    const measure=()=>setMapWidth(el.offsetWidth||null);
    measure();
    const observer=new ResizeObserver(measure);
    observer.observe(el);
    return ()=>observer.disconnect();
  },[]);
  const update=(key,value)=>setConfig(c=>({...c,[key]:value}));

  // Storm list for the picker, and the intact grid to draw before the first run.
  useEffect(() => {
    fetchStorms()
      .then(list => {
        setStorms(list.storms || []);
        const problem=checkNetwork(list.network);
        if(problem)throw new Error(`Unexpected network data: ${problem}`);
        setNetwork(list.network);
      })
      .catch(async err => {
        if (!(err instanceof OfflineError)) return setError(err.message);
        setOffline(true);
        try {
          const sample=await loadSample();
          const problem=checkRun(sample);
          if(problem)throw new Error(problem);
          setNetwork(sample.network);
        } catch {
          setError('The API is unreachable and the sample could not load. Start the combined preview server and reload.');
        }
      });
  }, []);

  const timeline = useMemo(() => (shown ? buildTimeline(shown) : null), [shown]);

  useEffect(() => { fetchRiskLayers().then(r => setRiskLayers(r.layers || [])).catch(() => {}); }, []);
  async function chooseRisk(id) {
    setRiskId(id);
    if (!id) return setRiskLayer(null);
    try { setRiskLayer(await fetchRiskLayer(id)); } catch (err) { setRiskLayer(null); setError(err.message); }
  }
  function loadRiskFile(profile) {
    const ids = network?.branches.map(b => b.id) || [];
    if (JSON.stringify(profile.branch_ids) !== JSON.stringify(ids)) throw new Error('Corridor-risk file branch order does not match the GB network.');
    setRiskId('file');
    setRiskLayer({id: 'file', name: profile.name || 'Imported corridor risk', source: profile.source, prediction: profile.prediction,
                  timestamps: profile.hours.map(h => h.timestamp), probability: profile.hours.map(h => h.failure_probability)});
  }
  // The run's clock at the playback hour, matched to the layer's hours; otherwise the storm total.
  const runStart = run?.storm?.timestamps?.[0] ?? run?.frames?.find(f => f.hour === 1)?.timestamp;
  const riskHour = timeline ? Math.min(timeline.lastHour, Math.max(0, Math.floor(t))) : null;
  const riskView = useMemo(() => {
    if (!riskLayer) return null;
    if (riskHour !== null && runStart) {
      const at = Date.parse(runStart) + riskHour * 3600e3;
      const i = riskLayer.timestamps.findIndex(x => Date.parse(x) === at);
      if (i >= 0) return {values: riskLayer.probability[i], mode: 'hour', at: riskLayer.timestamps[i]};
    }
    const values = riskLayer.probability[0].map((_, b) => 1 - riskLayer.probability.reduce((q, row) => q * (1 - row[b]), 1));
    return {values, mode: 'total'};
  }, [riskLayer, riskHour, runStart]);

  // Advance a continuous clock until the storm has passed. Time comes from
  // performance.now(), so motion stays smooth even if ticks are delayed. A timer
  // is used rather than requestAnimationFrame, which some embedded views never fire.
  useEffect(() => {
    if (!timeline || !playing) return undefined;
    const msPerHour = Math.min(1800, Math.max(250, PLAYBACK_MS / timeline.end));
    const started = performance.now();
    const initial=t;
    const id = setInterval(() => {
      const next = Math.min(timeline.end, initial + (performance.now() - started) / msPerHour);
      setT(next);
      if (next >= timeline.end) {clearInterval(id);setPlaying(false)}
    }, TICK_MS);
    return () => clearInterval(id);
  }, [timeline,playing]);

  async function onSimulate(request=null,nextPolicy='none') {
    setBusy(true);
    setError('');
    setPlaying(false);
    try {
      let result;
      try {
        const parameters=request||{...config,scenario,economics,weather:scenario==='imported'?importedWeather:null,
          network:scenario==='imported'?'gb':(network?.id||'gb')};
        result = await simulate(parameters.scenario,parameters);
        setOffline(false);
      } catch (err) {
        if (!(err instanceof OfflineError)) throw err;
        setOffline(true);
        if (request || scenario !== SYNTHETIC) throw new Error('Storm planning needs the backend running.');
        result = await loadSample();
      }
      const problem = checkRun(result);
      if (problem) throw new Error(`Unexpected simulation data: ${problem}`);
      setNetwork(result.network);setRunView(viewKey);
      setT(0);
      setRun(result);
      setPolicy(nextPolicy);setPlaying(true);
      if(request&&result.plan){setScenario(result.config.scenario);setImportedWeather(result.replay.request.weather);setConfig(c=>Object.fromEntries(Object.keys(c).map(k=>[k,result.config[k]])))}
      if(result.plan){setDraft(result.plan.portfolios.custom?.selected||result.plan.portfolios.optimized.selected);setEconomics(result.plan.config.economics)}
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
    const pylons = frame.status.filter(s => s === 'pylon'||s==='weather').length;
    const tripped = frame.status.filter(s => s === 'overload').length;
    const locked = frame.status.filter(s => s === 'transient').length;
    const parts = [plural(pylons, 'damaged route'), plural(tripped, 'overload trip'), ...(locked ? [plural(locked, 'fault lockout')] : [])];
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
      <div className="map-wrap" style={mapWidth ? {width: mapWidth} : undefined}>
        {network ? <UkMap network={network} run={shown} timeline={timeline} t={t} hardened={run?.plan?.portfolios[policy]?.selected} selected={asset} onSelect={setAsset} risk={network.id?null:riskView?.values} onView={onView} /> : !error && <p className="note">Loading grid…</p>}
      </div>

      <div className="panel">
        <div className="controls" ref={controlsRef}>
          <label className="visually-hidden" htmlFor="storm">
            Storm
          </label>
          <select id="storm" value={scenario} onChange={e => setScenario(e.target.value)} disabled={busy}>
            <option value={SYNTHETIC}>Synthetic storm</option>{importedWeather&&<option value="imported">Imported weather / predictions</option>}
            {storms.map(s => (
              <option key={s.id} value={s.id}>
                {s.name} · {month(s.start)}
              </option>
            ))}
          </select>
          <button type="button" className="simulate" onClick={()=>onSimulate()} disabled={busy || !network}>
            {busy ? 'Comparing plans…' : 'Simulate & compare'}
          </button>
        </div>
        <details className="scenario-settings"><summary>Scenario and planning settings</summary><div className="financial-fields">{[['seed','Random seed',0,2147483647],['hours','Synthetic hours',6,72],['peak_gust','Synthetic peak gust km/h',60,250],['crews','Repair crews',1,40],['budget','Budget units',0,12]].map(([key,label,min,max])=><label key={key}>{label}<input type="number" min={min} max={max} disabled={busy||(scenario!==SYNTHETIC&&['hours','peak_gust'].includes(key))} value={config[key]} onChange={e=>update(key,e.target.value===''?'':Number(e.target.value))}/></label>)}<label>Held-out scenarios<select value={config.samples} disabled={busy} onChange={e=>update('samples',Number(e.target.value))}>{[16,32,64,128].map(n=><option key={n}>{n}</option>)}</select></label><label>Synthetic preset<select value={config.storm_type} disabled={busy||scenario!==SYNTHETIC} onChange={e=>update('storm_type',e.target.value)}><option value="single_peak">Single wind peak</option><option value="persistent">Persistent winds</option><option value="double_peak">Two wind peaks</option></select></label></div><label>Import GB hourly weather / ML JSON<input type="file" accept=".json,application/json" disabled={busy} onChange={async event=>{const file=event.target.files?.[0];if(!file)return;try{const profile=JSON.parse(await file.text());if(profile.prediction_unit==='line_corridor_area_risk'){loadRiskFile(profile);setError('');return}setImportedWeather(profile);setScenario('imported');setError('')}catch{setError('Choose a valid weather JSON file.')}}}/></label><p className="note">Settings take effect on Simulate & compare. Applying a mitigation plan uses the completed storm’s settings and seed.</p></details>
        {network?.view && <p className="note">Simulated network: {network.buses.length} buses and {network.branches.length} branches, {network.view.buses_in_view} buses in view
          {network.id && network.view.graph_buses_in_view > network.view.buses_in_view ? ` (of ${network.view.graph_buses_in_view} mapped here; zoom in for more)` : ''}; the rest of GB is clustered.
          {viewChanged && <> <button type="button" className="link-button" onClick={()=>{setRun(null);setPlaying(false);setT(0)}}>Rebuild for this view</button></>}</p>}
        <div className="risk-layer">
          <label htmlFor="risk">Corridor outage risk</label>
          <select id="risk" value={riskId} onChange={e => chooseRisk(e.target.value)}>
            <option value="">Off</option>
            {riskLayers.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
            {riskId === 'file' && <option value="file">{riskLayer?.name}</option>}
          </select>
        </div>
        {riskView && (
          <p className="note risk-legend">
            <span className="risk-ramp" aria-hidden="true" />
            {riskView.mode === 'hour' ? `Hourly corridor outage risk at ${clock(riskView.at)} UTC` : 'Storm-total corridor outage risk (run hours don’t overlap this layer)'}
            {' '}· purple halos, 0.01%→30%. Area-level risk of a distribution fault within 5 km ({riskLayer.prediction.model_version}); shown alongside the engineering line model, not used as line-failure probability.
          </p>
        )}
        {timeline&&<div className="playback-controls"><button onClick={()=>{if(t>=timeline.end)setT(0);setPlaying(p=>!p)}}>{playing?'Pause':'Play'}</button><input aria-label="Storm playback hour" type="range" min="0" max={timeline.end} step="0.05" value={t} onChange={e=>{setPlaying(false);setT(Number(e.target.value))}}/><span>{portfolioNames[policy]||'Sample'}</span><button onClick={()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(run,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='gridguard-gb-plan.json';a.click();URL.revokeObjectURL(url)}}>Export</button></div>}
        {run?.storm && timeline ? (
          <StormChart benchmark={run.benchmark} timestamps={run.storm.timestamps} t={t} />
        ) : (
          <div className="progress" aria-hidden="true">
            <i style={{width: timeline ? `${(100 * t) / timeline.end}%` : 0}} />
          </div>
        )}
        {run&&<p className="note">Completed run: {run.storm?.name||run.input_source||'Synthetic windstorm'} · seed {run.config.seed} · {run.config.hours} hours. {run.plan&&'Green routes are reinforced.'}</p>}
        <p className="status">{status}</p>
        <p className="visually-hidden" aria-live="polite">
          {announcement}
        </p>
        {run?.storm && <Comparison run={run} />}
        {error && (
          <p className="note error" role="alert">
            {error}
          </p>
        )}
        {offline && <p className="note">Backend offline: using the legacy sample animation. Mitigation comparison is unavailable until the backend is running.</p>}
        <p className="credit">
          Grid © OpenStreetMap contributors (ODbL) via uk-grid-atlas
          {run?.storm && <> · Gusts: Met Office UKV via Open-Meteo.com (CC BY 4.0), © Crown copyright</>}
        </p>
      </div>
      <DecisionPanel run={run} policy={policy} setPolicy={key=>{setPlaying(false);setPolicy(key)}} draft={draft} setDraft={setDraft} economics={economics} setEconomics={setEconomics} busy={busy} asset={asset} setAsset={setAsset} rerun={()=>onSimulate(sameStormRequest(run,draft,economics),'custom')}/>
    </main>
  );
}

// One-line verdict: does the modelled risk land in the right hours and places?
function Comparison({run}) {
  const b = run.benchmark;
  const s = run.storm;
  const fixed = (v, d) => (Number.isFinite(v) ? v.toFixed(d) : '–');
  const hourLabel = i => (s.timestamps[i] ? `${clock(s.timestamps[i])}` : `hour ${i}`);
  return (
    <p className="note comparison">
      Real cuts peaked {hourLabel(b.peak_hour_observed)}, modelled risk {hourLabel(b.peak_hour_modelled)} (UTC); hourly
      correlation r = {fixed(b.timing_correlation, 2)}. Windiest third of areas ran at {fixed(b.rise_windiest_third, 1)}× their
      normal outage rate, calmest third {fixed(b.rise_calmest_third, 1)}× (ρ = {fixed(b.place_rank_correlation, 2)}). These are fixed weather-risk timing proxies, not a validation of mitigation outcomes. Dots are{' '}
      {s.operator} distribution faults, so this tests timing and place, not counts; shaded areas have outage data.
    </p>
  );
}
