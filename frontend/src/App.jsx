import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {OfflineError, fetchStorms, loadSample} from './api.js';
import {checkRun, checkNetwork} from './schema.js';
import {buildTimeline, frameIndexAt, layerTime} from './timeline.js';
import UkMap from './UkMap.jsx';
import StormChart from './StormChart.jsx';
import DecisionPanel from './DecisionPanel.jsx';
import {portfolioRun, sameStormRequest, portfolioNames} from './planning.js';
import StormControls from './storm-scenario/StormControls.jsx';
import {useStormScenario} from './storm-scenario/useStormScenario.js';
import {checkStorm} from './storm-scenario/config.js';
import {createStormClient, StormConnectionError} from './storm-scenario/client.js';

const stormClient = createStormClient();

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
  const storm = useStormScenario();
  const changeStorm = useCallback(value => {setPlaying(false);storm.setConfig(value);}, [storm.setConfig]);
  const changePlacement = useCallback(value => {setPlaying(false);storm.setPlacement(value);}, [storm.setPlacement]);
  const [jobStatus, setJobStatus] = useState('');
  const [busyMode, setBusyMode] = useState(null);
  const activeRequest = useRef(null);
  useEffect(() => () => activeRequest.current?.abort(), []);
  useEffect(() => {
    const pauseHidden = () => {if (document.hidden) setPlaying(false);};
    document.addEventListener('visibilitychange', pauseHidden);
    return () => document.removeEventListener('visibilitychange', pauseHidden);
  }, []);
  const shown=useMemo(()=>portfolioRun(run,policy),[run,policy]);
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

  async function onSimulate(request=null,nextPolicy='none',mode='compare') {
    if (busy) return;
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    setBusy(true);setBusyMode(mode);setJobStatus('queued');
    storm.setPlacement(null);
    setError('');
    setPlaying(false);
    try {
      const parameters=request||{...config,scenario,economics,weather:scenario==='imported'?importedWeather:null,
        ...(scenario===SYNTHETIC?{storm_config:storm.config}:{} )};
      if (parameters.storm_config) {
        const problem=checkStorm(parameters.storm_config);
        if(problem)throw new Error(problem);
      }
      const payload=mode==='single'?{storm_config:parameters.storm_config,crews:parameters.crews}:parameters;
      const result=await stormClient.run(payload,{mode,signal:controller.signal,onStatus:setJobStatus});
      setOffline(false);
      const problem = checkRun(result);
      if (problem) throw new Error(`Unexpected simulation data: ${problem}`);
      setNetwork(result.network);
      setT(0);
      setRun(result);
      setPolicy(nextPolicy);setPlaying(true);
      if(request&&result.plan){setScenario(result.config.scenario);setImportedWeather(result.replay.request.weather);setConfig(c=>({...c,...Object.fromEntries(Object.keys(c).filter(k=>result.config[k]!==undefined).map(k=>[k,result.config[k]]))}));if(result.config.storm_config)storm.setConfig(result.config.storm_config)}
      if(result.plan){setDraft(result.plan.portfolios.custom?.selected||result.plan.portfolios.optimized.selected);setEconomics(result.plan.config.economics)}
    } catch (err) {
      if (err instanceof StormConnectionError) setOffline(true);
      if (err.name !== 'AbortError') setError(err.name === 'TimeoutError' ? 'Storm run timed out. Reduce the duration or number of scenarios and retry.' : err.message);
    } finally {
      if (activeRequest.current === controller) {setBusy(false);setBusyMode(null);}
    }
  }

  let status = 'Pick a storm and press Simulate.';
  let announcement = '';
  if (timeline) {
    const frame = timeline.frames[frameIndexAt(timeline, t)];
    const pylons = frame.status.filter(s => s === 'pylon'||s==='weather').length;
    const tripped = frame.status.filter(s => s === 'overload').length;
    const parts = [plural(pylons, 'weather failure'), plural(tripped, 'line') + ' tripped'];
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
      <div className="map-wrap">
        {network ? <UkMap network={network} run={shown} timeline={timeline} t={t} hardened={run?.plan?.portfolios[policy]?.selected} selected={asset} onSelect={setAsset}
          stormEditor={scenario===SYNTHETIC&&!playing?{config:storm.config,onChange:storm.setConfig,placement:storm.placement,onPlace:storm.place,disabled:busy}:null} /> : !error && <p className="note">Loading grid…</p>}
      </div>

      <div className="panel">
        <div className="controls">
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
          {scenario===SYNTHETIC&&<button type="button" className="simulate" onClick={()=>onSimulate(null,'none','single')} disabled={busy || !network || !!checkStorm(storm.config)}>
            {busy&&busyMode==='single'?'Simulating…':'Simulate storm'}
          </button>}
          <button type="button" className={scenario===SYNTHETIC?'':'simulate'} onClick={()=>onSimulate()} disabled={busy || !network || (scenario===SYNTHETIC&&!!checkStorm(storm.config))}>
            {busy&&busyMode==='compare'?'Comparing plans…':'Compare mitigation'}
          </button>
        </div>
        {scenario===SYNTHETIC&&<StormControls value={storm.config} onChange={changeStorm} placement={storm.placement}
          onPlacementChange={changePlacement} message={storm.message} disabled={busy}/>}
        {busy&&<p className="note" role="status">{jobStatus==='queued'?'Queued on the simulation server…':'Running on the simulation server…'} {busyMode==='compare'?'Evaluating reinforcement and repair plans.':'Calculating hourly weather, failures and repairs.'}</p>}
        <details className="scenario-settings"><summary>Planning settings and weather import</summary><div className="financial-fields">
          {[['crews','Repair crews',1,40],['budget','Budget units',0,12],...(scenario===SYNTHETIC?[]:[['seed','Random seed',0,2147483647]])].map(([key,label,min,max])=><label key={key}>{label}<input type="number" min={min} max={max} disabled={busy} value={config[key]} onChange={e=>update(key,e.target.value===''?'':Number(e.target.value))}/></label>)}
          <label>Held-out scenarios<select value={config.samples} disabled={busy} onChange={e=>update('samples',Number(e.target.value))}>{[16,32,64,128].map(n=><option key={n}>{n}</option>)}</select></label>
        </div><label>Import GB hourly weather / ML JSON<input type="file" accept=".json,application/json" disabled={busy} onChange={async event=>{const file=event.target.files?.[0];if(!file)return;try{const profile=JSON.parse(await file.text());setImportedWeather(profile);setScenario('imported');setError('')}catch{setError('Choose a valid weather JSON file.')}}}/></label>
          <p className="note">Simulate storm runs one realization. Compare mitigation evaluates a bank of scenarios. Applying a plan reuses the completed comparison’s storm and seed.</p>
        </details>
        {timeline&&<div className="playback-controls"><button onClick={()=>{if(t>=timeline.end)setT(0);setPlaying(p=>!p)}}>{playing?'Pause':'Play'}</button><input aria-label="Storm playback hour" type="range" min="0" max={timeline.end} step="0.05" value={t} onChange={e=>{setPlaying(false);setT(Number(e.target.value))}}/><span>{run?.plan?(portfolioNames[policy]||'Plan'):'Single run'}</span><button onClick={()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(run,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='gridguard-gb-plan.json';a.click();URL.revokeObjectURL(url)}}>Export</button></div>}
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
        {offline && <p className="note">Simulation server unavailable. Start the backend to run your storm or compare mitigation plans.</p>}
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
