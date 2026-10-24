// GB storm simulator: wind -> pylons -> lines -> DC power flow.
// Workflow adapted from ICHack26 (MIT, Louis Tanak); see third_party/ICHack26-LICENSE.
import React, {useEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

const fmt=(x,d=0)=>Number(x||0).toLocaleString('en-GB',{maximumFractionDigits:d,minimumFractionDigits:d});
const pct=x=>x<.0001&&x>0?'<0.01%':`${fmt(100*x,x<.01?2:1)}%`;
const KV_WIDTH={400:2.1,275:1.5,132:.9};
const KV_COLOR={400:'#5da8bc',275:'#4b8a9f',132:'#3d6d7e'};
const W=760,H=860,PAD=24;
// Mirrors backend/storm.py so the inspector can show per-pylon odds at the current gust.
const erf=x=>{const t=1/(1+.3275911*Math.abs(x));const y=1-(((((1.061405429*t-1.453152027)*t)+1.421413741)*t-.284496736)*t+.254829592)*t*Math.exp(-x*x);return x>=0?y:-y};
const cAlt=(ele,h)=>1+.001*ele*Math.pow(10/Math.max(h,10),.2);  // UK NA to BS EN 1991-1-4
const pylonP=(g,m)=>g<=0?0:.5*(1+erf(Math.log(g/m.pylon_median_gust_kmh)/m.pylon_beta/Math.SQRT2));

function GridMap({network,frame,trail,selected,onSelect,real,hour}){
 const [hover,setHover]=useState(null);
 const geo=useMemo(()=>{const xs=network.buses.map(b=>b.x),ys=network.buses.map(b=>b.y);
  const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
  const k=Math.min((W-2*PAD)/(x1-x0),(H-2*PAD)/(y1-y0));const ox=(W-k*(x1-x0))/2-x0*k,oy=(H-k*(y1-y0))/2-y0*k;
  return {k,p:(x,y)=>[ox+x*k,oy+y*k],pts:network.buses.map(b=>[ox+b.x*k,oy+b.y*k])}},[network]);
 const {pts,p,k}=geo;
 const status=i=>frame?.status[i]||'online';
 const hb=hover?.startsWith('B')?network.buses[Number(hover.slice(1))-1]:null;const hl=hover&&!hb?network.branches.find(e=>e.id===hover):null;
 return <div className="map">
  <svg viewBox={`0 0 ${W} ${H}`} role="group" aria-label="Great Britain transmission network">
   <defs><radialGradient id="storm"><stop offset="0" stopColor="#9fb7cc" stopOpacity=".42"/><stop offset=".55" stopColor="#7b95aa" stopOpacity=".16"/><stop offset="1" stopColor="#7b95aa" stopOpacity="0"/></radialGradient></defs>
   {trail.length>1&&<polyline points={trail.map(c=>p(...c).join(',')).join(' ')} fill="none" stroke="#7b95aa" strokeDasharray="3 6" opacity=".45"/>}
   {real&&<RealLayers real={real} hour={hour} p={p}/>}
   {frame?.storm&&<circle cx={p(...frame.storm)[0]} cy={p(...frame.storm)[1]} r={frame.storm_radius*k*2.4} fill="url(#storm)"/>}
   {network.branches.map((e,i)=>{const s=status(i);const load=frame?.loading[i]||0;
    // Real OSM route, with its ends pinned to the clustered substation positions.
    const route=[pts[e.from-1],...e.geometry.slice(1,-1).map(c=>p(...c)),pts[e.to-1]].map(c=>c.join(',')).join(' ');
    const color=s==='pylon'?'#ed8792':s==='overload'?'#ffb36b':load>.9?'#ffd27a':KV_COLOR[e.kv];const on=selected===e.id||hover===e.id;
    return <g key={e.id} className="hit" onClick={()=>onSelect(e.id)} onMouseEnter={()=>setHover(e.id)} onMouseLeave={()=>setHover(null)}>
     {on&&<polyline points={route} fill="none" stroke={color} strokeWidth="9" opacity=".25" strokeLinecap="round" strokeLinejoin="round"/>}
     <polyline points={route} fill="none" stroke={color} strokeWidth={KV_WIDTH[e.kv]} strokeDasharray={s!=='online'?'3 4':undefined} strokeLinecap="round" strokeLinejoin="round"/>
     <polyline points={route} fill="none" stroke="transparent" strokeWidth="8"/>
    </g>})}
   {network.buses.map((b,i)=>{const [x,y]=pts[i];const served=frame?.served_by_bus[i]??b.load_mw;const dark=b.load_mw-served>.5;
    return <circle key={b.id} className="hit" cx={x} cy={y} r={Math.max(1.6,Math.min(5.5,Math.sqrt(b.load_mw)/7))+(selected===`B${b.id}`?2.5:0)}
     fill={dark?'#ed8792':'#9ab9c4'} opacity={dark?1:.7} stroke={selected===`B${b.id}`?'#fff':'none'}
     onClick={()=>onSelect(`B${b.id}`)} onMouseEnter={()=>setHover(`B${b.id}`)} onMouseLeave={()=>setHover(null)}/>})}
   {real&&<OutageDots real={real} hour={hour} p={p}/>}
   {frame&&network.branches.map((e,i)=>{const f=frame.fallen_at[i];if(f==null)return null;const [x,y]=p(f[0],f[1]);
    return <g key={e.id} className="hit" onClick={()=>onSelect(e.id)}><path d={`M${x-5} ${y-5}L${x+5} ${y+5}M${x+5} ${y-5}L${x-5} ${y+5}`} stroke="#ff6b7d" strokeWidth="2.4" strokeLinecap="round"/></g>})}
  </svg>
  {hl&&<div className="tip"><b>{hl.name||hl.id}</b> {hl.kv} kV · {fmt(hl.length_km)} km · {fmt(hl.pylons)} pylons · up to {fmt(hl.ele_max_m)} m</div>}
  {hb&&<div className="tip"><b>Substation {hb.id}</b> {hb.kv} kV · {fmt(hb.ele_m)} m · {fmt(frame?.served_by_bus[hb.id-1]??hb.load_mw)} / {fmt(hb.load_mw)} MW supplied</div>}
 </div>
}

// Real storm: measured-model gust field and the outage feed's coverage, drawn under the grid.
function RealLayers({real,hour,p}){
 const [cw,ch]=real.weather.cell,gusts=hour>0?real.weather.gust_kmh[hour-1]:null;
 const [vw,vh]=real.coverage.cell;const a=p(0,0),b=p(cw,ch),c=p(vw,vh);const w=b[0]-a[0],h=b[1]-a[1],cvw=c[0]-a[0],cvh=c[1]-a[1];
 return <g aria-hidden="true">
  {real.coverage.cells.map(([x,y],i)=>{const [px,py]=p(x,y);return <rect key={i} x={px-cvw/2} y={py-cvh/2} width={cvw} height={cvh} fill="#ffd27a" opacity=".045"/>})}
  {gusts&&real.weather.nodes.map(([x,y],i)=>{const g=gusts[i];if(g==null||g<50)return null;const [px,py]=p(x,y);
   return <rect key={i} x={px-w/2} y={py-h/2} width={w} height={h} fill={g>=110?'#e6f1f8':g>=90?'#a9c8de':'#6f93ad'} opacity={Math.min(.5,(g-50)/120)}/>})}
 </g>
}

function OutageDots({real,hour,p}){
 return <g aria-hidden="true">{real.outages.map(([x,y,h],i)=>{if(h>=hour)return null;const [px,py]=p(x,y);const fresh=hour-1-h<2;
  return <circle key={i} cx={px} cy={py} r={fresh?2.6:1.5} fill="#ffd27a" opacity={fresh?.95:.35}/>})}</g>
}

// Hourly real outage starts (bars) vs modelled expected pylon collapses (line), both scaled to their own peak.
function TimingChart({bench,hour}){
 const n=bench.observed_per_hour.length,W2=1000,H2=46,mo=Math.max(...bench.observed_per_hour),mm=Math.max(...bench.modelled_per_hour)||1;
 const line=bench.modelled_per_hour.map((v,i)=>`${(i+.5)/n*W2},${H2-v/mm*(H2-4)}`).join(' ');
 return <svg className="timing" viewBox={`0 0 ${W2} ${H2}`} preserveAspectRatio="none" aria-label="Real outages per hour versus modelled risk">
  {bench.observed_per_hour.map((v,i)=><rect key={i} x={i/n*W2+1} y={H2-v/mo*(H2-4)} width={W2/n-2} height={v/mo*(H2-4)} fill="#ffd27a" opacity={i<hour?.75:.3}/>)}
  <polyline points={line} fill="none" stroke="#5da8bc" strokeWidth="2" vectorEffect="non-scaling-stroke"/>
  {hour>0&&<line x1={(hour-.5)/n*W2} x2={(hour-.5)/n*W2} y1="0" y2={H2} stroke="#fff" strokeWidth="1" vectorEffect="non-scaling-stroke" opacity=".6"/>}
 </svg>
}

function Benchmark({run}){
 const b=run.benchmark,s=run.storm,t=i=>new Date(s.timestamps[i]).toLocaleString('en-GB',{weekday:'short',hour:'2-digit',minute:'2-digit',timeZone:'UTC'});
 const total=b.observed_per_hour.reduce((a,c)=>a+c,0);
 return <div className="bench">
  <p><b>{fmt(total)}</b> real {s.operator} outages started in these {b.observed_per_hour.length} hours <span className="muted">({fmt(s.reissued_dropped)} duplicate feed ids removed)</span>.</p>
  <p><b>When:</b> real outages peaked {t(b.peak_hour_observed)}, modelled pylon risk peaked {t(b.peak_hour_modelled)} (UTC). Hour-by-hour correlation <b>r = {fmt(b.timing_correlation,2)}</b>.</p>
  <p><b>Where:</b> in the windiest third of areas outages ran at <b>{fmt(b.rise_windiest_third,1)}×</b> their normal rate, in the calmest third <b>{fmt(b.rise_calmest_third,1)}×</b>. Rank correlation of peak gust vs. rise across {b.cells.length} areas: <b>ρ = {fmt(b.place_rank_correlation,2)}</b>.</p>
  <p><b>Transmission:</b> the sim expects {b.modelled_collapses_in_coverage<.01?<>essentially no pylon collapses (<b>{b.modelled_collapses_in_coverage.toExponential(1)}</b>)</>:<><b>{fmt(b.modelled_collapses_in_coverage,2)}</b> pylon collapses</>} inside the {s.operator} area: Darragh's gusts stayed well below what topples a lattice tower. The blue risk line is scaled to its own peak to show its timing.</p>
  <p className="muted">Outages are local distribution faults (trees on 11 kV lines etc.), not transmission pylons, so this checks timing and place, not counts. Outage data only covers {s.operator}'s licence areas (shaded). Gusts: Met Office UKV via Open-Meteo, adjusted from model terrain to real route height.</p>
 </div>
}

function Inspector({network,frame,model,selected}){
 const i=network.branches.findIndex(e=>e.id===selected);
 if(i>=0){const e=network.branches[i];const g=frame?.gust_kmh[i]||0;const s=frame?.status[i]||'online';
  return <div className="inspector"><span className="eyebrow">LINE {e.id}</span><h3>{e.name||`Substation ${e.from} ↔ ${e.to}`}</h3><dl>
   <dt>Voltage</dt><dd>{e.kv} kV</dd><dt>Route length</dt><dd>{fmt(e.length_km,1)} km</dd>
   <dt>Pylons</dt><dd>{fmt(e.pylons)} <small>{e.tower_height_m} m tall @ {e.span_m} m span</small></dd>
   <dt>Terrain along route</dt><dd>{fmt(e.ele_mean_m)} m avg <small>/ {fmt(e.ele_max_m)} m max</small></dd>
   <dt>Wind speed-up at highest pylon</dt><dd>×{fmt(cAlt(e.ele_max_m,e.tower_height_m),2)}</dd>
   <dt>Status</dt><dd className={s!=='online'?'bad':''}>{s==='pylon'?`Pylon down at ${fmt(frame.fallen_at[i][2])} m elevation`:s==='overload'?'Tripped on overload':'In service'}</dd>
   <dt>Peak gust on route</dt><dd>{fmt(g)} km/h</dd>
   <dt>Worst-placed pylon falls this hour</dt><dd>{pct(model?pylonP(g,model):0)}</dd>
   <dt>Any pylon falls this hour</dt><dd>{pct(frame?.line_failure_probability[i]||0)}</dd>
   <dt>Power flow</dt><dd>{fmt(Math.abs(frame?.flows_mw[i]||0))} MW <small>/ {fmt(e.rating_mw)}</small></dd>
  </dl></div>}
 if(selected?.startsWith('B')){const b=network.buses[Number(selected.slice(1))-1];const served=frame?.served_by_bus[b.id-1]??b.load_mw;
  return <div className="inspector"><span className="eyebrow">SUBSTATION {b.id}</span><h3>{b.kv} kV · {b.lat.toFixed(2)}°, {b.lon.toFixed(2)}°</h3><dl>
   <dt>Elevation</dt><dd>{fmt(b.ele_m)} m</dd>
   <dt>Demand</dt><dd>{fmt(b.load_mw)} MW</dd><dt>Supplied</dt><dd className={b.load_mw-served>.5?'bad':''}>{fmt(served)} MW</dd>
   <dt>Lines</dt><dd>{network.branches.filter(e=>e.from===b.id||e.to===b.id).length}</dd></dl></div>}
 return <div className="inspector"><p className="muted">Click a line or substation.</p></div>
}

function App(){
 const [params,setParams]=useState({peak_gust:170,hours:36,seed:1});
 const [run,setRun]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [hour,setHour]=useState(0),[playing,setPlaying]=useState(false),[selected,setSelected]=useState(null);
 const [scenario,setScenario]=useState('synthetic'),[realSeed,setRealSeed]=useState(1);
 const set=(k,v)=>setParams(p=>({...p,[k]:v}));
 useEffect(()=>{const t=setTimeout(async()=>{setBusy(true);setError('');try{
   const body=scenario==='synthetic'?params:{scenario,seed:realSeed};
   const r=await fetch('/api/storm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
   const data=await r.json();if(!r.ok)throw new Error(data.error);setRun(data);setHour(h=>Math.min(h,data.config.hours));
  }catch(e){setError(e.message==='Failed to fetch'?'Cannot reach the backend on port 5001.':e.message)}finally{setBusy(false)}},250);return()=>clearTimeout(t)},[params,scenario,realSeed]);
 const hours=run?.config.hours??params.hours;const real=run?.storm?run:null;
 const timer=useRef(null);
 useEffect(()=>{if(!playing)return;timer.current=setInterval(()=>setHour(h=>{if(h>=hours){setPlaying(false);return h}return h+1}),real?300:450);return()=>clearInterval(timer.current)},[playing,hours,real]);
 const frame=run?.frames[Math.min(hour,run.frames.length-1)];
 const trail=run?run.frames.slice(1,hour+1).map(f=>f.storm):[];
 const down=frame?frame.status.filter(s=>s==='pylon').length:0,tripped=frame?frame.status.filter(s=>s==='overload').length:0;
 const log=run?run.frames.slice(1,hour+1).flatMap(f=>f.events.filter(e=>e.type==='pylon').map(e=>({...e,hour:f.hour}))).reverse():[];
 const name=id=>{const e=run.network.branches.find(b=>b.id===id);return e.name||id};
 const toggle=()=>{if(!run)return;if(hour>=hours)setHour(0);setPlaying(p=>!p)};
 const when=real&&hour>0?new Date(real.storm.timestamps[hour-1]).toLocaleString('en-GB',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'UTC'})+' UTC':null;
 useEffect(()=>{const key=e=>{if(e.code==='Space'&&!['INPUT','SELECT','BUTTON'].includes(e.target.tagName)){e.preventDefault();toggle()}if(e.key==='Escape')setSelected(null)};
  window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key)});
 return <div className="app">
  {run?<GridMap network={run.network} frame={frame} trail={real?[]:trail} selected={selected} onSelect={setSelected} real={real} hour={hour}/>:<div className="map loading">Loading grid…</div>}
  <div className="readout">{when||`h${hour}`}{real&&<> · <span className="warn">{fmt(real.outages.filter(o=>o[2]<hour).length)} real outages</span></>} · <span className="bad">{down} down</span>{busy&&<span className="muted"> · simulating…</span>}<small>Lines © OpenStreetMap contributors (ODbL) via uk-grid-atlas</small></div>
  {error&&<div className="error">{error}</div>}
  <aside className="overlay controls">
   <div className="brand">⚡ GridGuard <span className="muted">storm vs. the GB grid</span></div>
   <div className="tabs" role="tablist"><button role="tab" aria-selected={scenario==='synthetic'} className={scenario==='synthetic'?'on':''} onClick={()=>{setScenario('synthetic');setHour(0)}}>Synthetic storm</button><button role="tab" aria-selected={scenario!=='synthetic'} className={scenario!=='synthetic'?'on':''} onClick={()=>{setScenario('darragh-2024');setHour(0)}}>Real: Storm Darragh</button></div>
   {scenario==='synthetic'?<>
    <label>Peak gust at sea level <b>{params.peak_gust} km/h</b><input type="range" min="60" max="250" step="5" value={params.peak_gust} onChange={e=>set('peak_gust',Number(e.target.value))}/></label>
    <label>Storm duration<select value={params.hours} onChange={e=>set('hours',Number(e.target.value))}>{[12,24,36,48,72].map(h=><option key={h} value={h}>{h} hours</option>)}</select></label>
    <label>Storm seed<div className="row"><input type="number" min="0" value={params.seed} onChange={e=>set('seed',Math.max(0,Number(e.target.value)||0))}/><button onClick={()=>set('seed',Math.floor(Math.random()*1e6))}>New storm</button></div></label>
   </>:<>
    <p className="howto">Replays Storm Darragh (6–8 Dec 2024) with real hourly gusts, then compares with the real power cuts recorded at the same time.</p>
    <label>Failure-draw seed<div className="row"><input type="number" min="0" value={realSeed} onChange={e=>setRealSeed(Math.max(0,Number(e.target.value)||0))}/><button onClick={()=>setRealSeed(Math.floor(Math.random()*1e6))}>Re-roll</button></div></label>
    {real&&<Benchmark run={real}/>}
   </>}
   <details className="howto"><summary>How lines fail</summary>
    <p>Pylons per line = route length ÷ typical span (400 kV 360 m, 275 kV 350 m, 132 kV 280 m).{run&&<> {fmt(run.network.branches.reduce((s,e)=>s+e.pylons,0))} pylons in total.</>}</p>
    <p>Each pylon has a {run?fmt(run.model.pylon_median_gust_kmh):250} km/h median collapse gust (lognormal, β {run?.model.pylon_beta??.12}). Gust is sampled every ~2 km along each real OSM route ({run?fmt(run.model.route_samples):'~6,000'} points) and raised with terrain height using the UK Eurocode altitude factor: 1 + 0.001 × elevation × (10 / tower height)^0.2, so a pylon at 400 m sees ~30% more wind than one at sea level. Terrain from EU-DEM (25 m). One pylon down takes the line out for the rest of the storm.</p>
    <p>After each collapse a DC power flow reroutes power; lines pushed past their rating trip, and are reclosed the next hour.</p>
    <p className="muted">Spans, fragility, ratings and demand are illustrative, not utility data.</p></details>
  </aside>
  <div className="overlay playback">
   <button onClick={toggle} disabled={!run} aria-label={playing?'Pause':'Play'}>{playing?'❚❚':'▶'}</button>
   <div className="scrub">{real&&<TimingChart bench={real.benchmark} hour={hour}/>}<input type="range" min="0" max={hours} value={hour} onChange={e=>{setPlaying(false);setHour(Number(e.target.value))}} aria-label="Hour"/></div>
   <div className="status"><span>{when?<b>{when}</b>:<>Hour <b>{hour}</b> / {hours}</>}</span>{real&&<span><i className="key bars"/>real outages/hour <i className="key line"/>modelled pylon risk <i className="key gust1"/>gust 70+ <i className="key gust2"/>90+ <i className="key gust3"/>110+ km/h</span>}<span className="bad">{down} pylon{down===1?'':'s'} down</span><span className="warn">{tripped} tripped</span><span>{fmt(frame?.shed_mw||0)} MW off</span></div>
  </div>
  <aside className="overlay log"><span className="eyebrow">PYLON COLLAPSES</span>{log.length?log.map((e,i)=><button key={i} onClick={()=>setSelected(e.line)}><b>h{e.hour}</b> {name(e.line)} <small>{fmt(e.gust_kmh)} km/h · {fmt(e.ele_m)} m</small></button>):<p className="muted">None yet.</p>}</aside>
  {run&&selected&&<div className="inspector-wrap"><button className="close" onClick={()=>setSelected(null)} aria-label="Close">×</button><Inspector network={run.network} frame={frame} model={run.model} selected={selected}/></div>}
 </div>
}

createRoot(document.getElementById('root')).render(<App/>);
