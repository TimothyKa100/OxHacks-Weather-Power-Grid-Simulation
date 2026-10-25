import React, {memo, useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {mapFrame, outline} from './geo.js';
import {flashesAt, gustsAt, layerTime, mix, stateAt, stormAt} from './timeline.js';

const LINE = {400: '#a9bccb', 275: '#8399aa', 132: '#617888'};
const WIDTH = {400: 1.5, 275: 1.1, 132: 0.75};
const DOWN = {weather: '#e5484d', pylon: '#e5484d', overload: '#f2994a'};
const BUS = {powered: '#c9d6e2', dark: '#e5484d'};
const OUTAGE = '#ffd27a';
const lineColor = (status, kv) => (status === 'online' ? LINE[kv] || LINE[275] : DOWN[status] || DOWN.pylon);
const isDark = (frame, i, load) => frame.node_status
  ? ['out_of_supply','reduced_supply','deenergized'].includes(frame.node_status[i])
  : load > 0 && frame.served_by_bus[i] < load - 0.5;
const STATUS_TEXT = {weather: 'weather failure', online: 'in service', pylon: 'pylon down', overload: 'tripped on overload'};
const quantize = (x, step) => Math.round(x / step) * step;
const MAX_ZOOM = 12;
const DRAG_PX = 4;

// Everything is drawn from the simulation clock t, not CSS transitions, so the
// map always shows exactly the state at time t.
// Corridor outage risk: log scale from 1e-4 to 0.3 (per hour, or per storm for totals).
const RISK_LOW = '#3a2d5c';
const RISK_HIGH = '#d08cff';
export const riskLevel = p => Math.min(1, Math.max(0, (Math.log10(Math.max(p, 1e-4)) + 4) / (4 + Math.log10(0.3))));

export default function UkMap({network, run, timeline, t, hardened=[], selected, onSelect, risk}) {
  const geo = useMemo(() => mapFrame(network.buses), [network]);
  const shapes = useMemo(() => {
    const index = new Map(network.buses.map((b, i) => [b.id, i]));
    const buses = network.buses.map(b => geo.px(b.x, b.y));
    const routes = network.branches.map(e => {
      // Real route where the backend has one, with its ends pinned to the substations.
      const ends = [buses[index.get(e.from)], buses[index.get(e.to)]];
      const middle = (e.geometry || []).slice(1, -1).map(([x, y]) => geo.px(x, y));
      const points = [ends[0], ...middle, ends[1]];
      return {points: points.map(p => p.map(v => v.toFixed(1)).join(',')).join(' '), mid: points[Math.floor(points.length / 2)]};
    });
    return {buses, routes};
  }, [network, geo]);

  const state = timeline ? stateAt(timeline, t) : null;
  const fadeStep = state ? quantize(state.fade, 0.1) : 1;
  const storm = timeline ? stormAt(timeline, t) : null;
  const {wrapRef, svgRef, box, view, zoomBy, reset} = useZoom(outline.width, outline.height);
  // Marks grow slower than the zoom so detail separates instead of just getting bigger.
  const s = view.k ** -0.75;

  return (
    <div className="uk-map-zoom" ref={wrapRef}>
    <div className="uk-map-box" style={box ? {width: box.w, height: box.h} : undefined}>
    <svg ref={svgRef} className="uk-map" viewBox={view.box.join(' ')} role="img" aria-label="Map of the GB transmission grid">
      <defs>
        <radialGradient id="storm">
          <stop offset="0" stopColor="#9fb7cc" stopOpacity="0.45" />
          <stop offset="0.55" stopColor="#7b95aa" stopOpacity="0.16" />
          <stop offset="1" stopColor="#7b95aa" stopOpacity="0" />
        </radialGradient>
      </defs>
      <path d={outline.path} className="land" />
      {run?.coverage && <Coverage coverage={run.coverage} geo={geo} />}
      {run?.weather && timeline && <GustField weather={run.weather} geo={geo} t={layerTime(t)} />}
      {storm && (
        <circle
          cx={geo.px(storm.x, storm.y)[0]}
          cy={geo.px(storm.x, storm.y)[1]}
          r={2.4 * storm.radius * geo.pxPerUnit}
          fill="url(#storm)"
          opacity={storm.alpha * (0.4 + 0.6 * storm.strength)}
          pointerEvents="none"
        />
      )}
      {risk && <CorridorRisk network={network} routes={shapes.routes} risk={risk} s={s} />}
      <Lines network={network} routes={shapes.routes} state={state} fadeStep={fadeStep} geo={geo} hardened={hardened} selected={selected} onSelect={onSelect} s={s} />
      <Buses network={network} positions={shapes.buses} state={state} fadeStep={fadeStep} s={s} />
      {run?.outages && timeline && <Outages outages={run.outages} times={timeline.outageTimes} geo={geo} t={layerTime(t)} s={s} />}
      {timeline &&
        flashesAt(timeline, t).map(f => {
          const [x, y] = f.at ? geo.px(f.at[0], f.at[1]) : shapes.routes[f.branch].mid;
          return (
            <circle
              key={f.branch}
              cx={x}
              cy={y}
              r={(2 + 16 * f.progress) * s}
              fill="none"
              stroke={DOWN[f.status] || DOWN.pylon}
              strokeWidth={1.5 * s}
              opacity={1 - f.progress}
              pointerEvents="none"
            />
          );
        })}
    </svg>
      <div className="zoom-controls">
        <button type="button" onClick={() => zoomBy(2)} disabled={view.k >= MAX_ZOOM} aria-label="Zoom in">+</button>
        <button type="button" onClick={() => zoomBy(0.5)} disabled={view.k <= 1} aria-label="Zoom out">−</button>
        <button type="button" onClick={reset} disabled={view.k <= 1} aria-label="Reset zoom">⟲</button>
      </div>
    </div>
    </div>
  );
}

// Wheel/pinch zoom about the cursor and drag to pan, by moving the viewBox.
// A drag swallows the click that ends it, so panning never selects a line.
// The frame (box, in px) fills its container, usually wider than the map's own aspect;
// at k=1 the whole map fits in it, and zooming fills the extra width.
function useZoom(width, height) {
  const wrapRef = useRef(null);
  const svgRef = useRef(null);
  const [box, setBox] = useState(null);
  const [center, setCenter] = useState({cx: width / 2, cy: height / 2, k: 1});
  // viewBox size at k=1: the whole map, stretched to the frame's aspect.
  const base = useMemo(() => {
    if (!box) return [width, height];
    const u = Math.max(width / box.w, height / box.h);
    return [box.w * u, box.h * u];
  }, [box, width, height]);
  const clamp = useCallback(({cx, cy, k}) => {
    k = Math.min(MAX_ZOOM, Math.max(1, k));
    const axis = (c, span, size) => (span >= size ? size / 2 : Math.min(size - span / 2, Math.max(span / 2, c)));
    return {k, cx: axis(cx, base[0] / k, width), cy: axis(cy, base[1] / k, height)};
  }, [base, width, height]);
  const zoomAt = useCallback((factor, px, py) => setCenter(v => {
    const k = Math.min(MAX_ZOOM, Math.max(1, v.k * factor));
    return clamp({k, cx: px - (px - v.cx) * v.k / k, cy: py - (py - v.cy) * v.k / k});
  }), [clamp]);
  const zoomBy = useCallback(factor => setCenter(v => clamp({...v, k: v.k * factor})), [clamp]);
  const pan = useCallback((dx, dy) => setCenter(v => clamp({...v, cx: v.cx + dx, cy: v.cy + dy})), [clamp]);
  const reset = useCallback(() => setCenter({cx: width / 2, cy: height / 2, k: 1}), [width, height]);
  useEffect(() => setCenter(v => clamp(v)), [clamp]);

  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return undefined;
    const measure = () => {
      const W = wrap.clientWidth, H = wrap.clientHeight;
      if (!W || !H) return;
      setBox(b => (b && b.w === W && b.h === H ? b : {w: W, h: H}));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(wrap);
    return () => observer.disconnect();
  }, []);

  const vw = base[0] / center.k, vh = base[1] / center.k;
  const view = {k: center.k, box: [center.cx - vw / 2, center.cy - vh / 2, vw, vh]};

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return undefined;
    const toSvg = (clientX, clientY) => {
      const p = new DOMPoint(clientX, clientY).matrixTransform(svg.getScreenCTM().inverse());
      return [p.x, p.y];
    };
    const unitsPerPx = () => 1 / svg.getScreenCTM().a;
    const pointers = new Map();
    let dragged = false;
    let pinch = null;

    const onWheel = e => {
      e.preventDefault();
      zoomAt(Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.002)), ...toSvg(e.clientX, e.clientY));
    };
    const onDown = e => {
      if (e.button !== 0 && e.pointerType === 'mouse') return;
      pointers.set(e.pointerId, {x: e.clientX, y: e.clientY, x0: e.clientX, y0: e.clientY});
      if (pointers.size === 1) dragged = false;
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        pinch = Math.hypot(a.x - b.x, a.y - b.y);
      }
    };
    const onMove = e => {
      const p = pointers.get(e.pointerId);
      if (!p) return;
      const dx = e.clientX - p.x, dy = e.clientY - p.y;
      p.x = e.clientX; p.y = e.clientY;
      if (!dragged && Math.hypot(p.x - p.x0, p.y - p.y0) > DRAG_PX) {
        dragged = true;
        svg.setPointerCapture(e.pointerId);
      }
      if (!dragged) return;
      if (pointers.size === 1) {
        const u = unitsPerPx();
        pan(-dx * u, -dy * u);
      } else if (pointers.size === 2 && pinch) {
        const [a, b] = [...pointers.values()];
        const dist = Math.hypot(a.x - b.x, a.y - b.y);
        zoomAt(dist / pinch, ...toSvg((a.x + b.x) / 2, (a.y + b.y) / 2));
        pinch = dist;
      }
    };
    const onUp = e => {
      pointers.delete(e.pointerId);
      if (pointers.size < 2) pinch = null;
    };
    const onClick = e => {
      if (dragged) { e.stopPropagation(); e.preventDefault(); dragged = false; }
    };
    const onDblClick = e => zoomAt(2, ...toSvg(e.clientX, e.clientY));

    svg.addEventListener('wheel', onWheel, {passive: false});
    svg.addEventListener('pointerdown', onDown);
    svg.addEventListener('pointermove', onMove);
    svg.addEventListener('pointerup', onUp);
    svg.addEventListener('pointercancel', onUp);
    svg.addEventListener('click', onClick, true);
    svg.addEventListener('dblclick', onDblClick);
    return () => {
      svg.removeEventListener('wheel', onWheel);
      svg.removeEventListener('pointerdown', onDown);
      svg.removeEventListener('pointermove', onMove);
      svg.removeEventListener('pointerup', onUp);
      svg.removeEventListener('pointercancel', onUp);
      svg.removeEventListener('click', onClick, true);
      svg.removeEventListener('dblclick', onDblClick);
    };
  }, [pan, zoomAt]);

  return {wrapRef, svgRef, box, view, zoomBy, reset};
}

// Lines and buses only change at an hourly event and during its short fade.
const Lines = memo(function Lines({network, routes, state, geo, hardened, selected, onSelect, s=1}) {
  return (
    <g>
      {network.branches.map((e, i) => {
        const status = state?.frame.status[i] ?? 'online';
        const before = state?.previous.status[i] ?? status;
        const down = status !== 'online';
        const fallen = status === 'pylon' ? state.frame.fallen_at?.[i] : null;
        const at = fallen ? geo.px(fallen[0], fallen[1]) : null;
        return (
          <g key={e.id} role="button" tabIndex={0} aria-label={`Inspect ${e.id}`} onClick={()=>onSelect?.(e.id)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();onSelect?.(e.id)}}}>
            <polyline
              points={routes[i].points}
              fill="none"
              stroke={selected===e.id?'#fff':status==='online'&&hardened?.includes(e.id)?'#a8dc81':mix(lineColor(before, e.kv), lineColor(status, e.kv), state ? state.fade : 1)}
              strokeWidth={((WIDTH[e.kv] || 1)+(hardened?.includes(e.id)?1:0)+(selected===e.id?1:0)) * s}
              strokeDasharray={down ? `${3 * s} ${2.5 * s}` : undefined}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <title>
                {e.id} · {e.name || 'Transmission branch'}
                {e.kv ? ` · ${e.kv} kV` : ''} · {STATUS_TEXT[status] || status}
              </title>
            </polyline>
            <polyline points={routes[i].points} fill="none" stroke="transparent" strokeWidth={9 * s} />
            {at && (
              <path
                d={`M${at[0] - 3 * s},${at[1] - 3 * s}L${at[0] + 3 * s},${at[1] + 3 * s}M${at[0] + 3 * s},${at[1] - 3 * s}L${at[0] - 3 * s},${at[1] + 3 * s}`}
                stroke={DOWN.pylon}
                strokeWidth={1.6 * s}
                strokeLinecap="round"
              >
                <title>Pylon down at {Math.round(fallen[2] ?? 0)} m elevation</title>
              </path>
            )}
          </g>
        );
      })}
    </g>
  );
}, (a, b) => a.network === b.network && a.routes === b.routes && a.state?.i === b.state?.i && a.fadeStep === b.fadeStep && a.hardened === b.hardened && a.selected === b.selected && a.onSelect === b.onSelect && a.s === b.s);

const Buses = memo(function Buses({network, positions, state, s=1}) {
  return (
    <g>
      {network.buses.map((b, i) => {
        const served = state ? state.frame.served_by_bus[i] : b.load_mw;
        const darkNow = state ? isDark(state.frame, i, b.load_mw) : false;
        const darkBefore = state ? isDark(state.previous, i, b.load_mw) : false;
        const fill = mix(darkBefore ? BUS.dark : BUS.powered, darkNow ? BUS.dark : BUS.powered, state ? state.fade : 1);
        return (
          <circle key={b.id} data-node-status={state?.frame.node_status?.[i]||'supplied'} cx={positions[i][0]} cy={positions[i][1]} r={Math.max(1.2, Math.min(3.2, Math.sqrt(b.load_mw) / 9)) * s} fill={fill} className="bus" style={{strokeWidth: 0.6 * s}}>
            <title>
              Substation {b.id}
              {b.kv ? ` · ${b.kv} kV` : ''}: {state?.frame.node_status?.[i]?.replaceAll('_',' ')||(darkNow ? 'losing supply' : 'supplied')}
              {` · ${served.toFixed(1)} / ${b.load_mw.toFixed(1)} MW served`}
            </title>
          </circle>
        );
      })}
    </g>
  );
}, (a, b) => a.network === b.network && a.positions === b.positions && a.state?.i === b.state?.i && a.fadeStep === b.fadeStep && a.s === b.s);

// Wide halos under each route: area-level outage risk in the line's corridor, separate from line state.
const CorridorRisk = memo(function CorridorRisk({network, routes, risk, s=1}) {
  return (
    <g aria-hidden="true" pointerEvents="none">
      {network.branches.map((e, i) => {
        const level = riskLevel(risk[i] ?? 0);
        if (level <= 0) return null;
        return (
          <polyline key={e.id} points={routes[i].points} fill="none" stroke={mix(RISK_LOW, RISK_HIGH, level)}
            strokeOpacity={0.25 + 0.6 * level} strokeWidth={(3 + 7 * level) * s} strokeLinecap="round" strokeLinejoin="round" />
        );
      })}
    </g>
  );
});

// Where the outage feeds report at all: outside it, "no dots" means "no data".
const Coverage = memo(function Coverage({coverage, geo}) {
  const [w, h] = coverage.cell;
  return (
    <g aria-hidden="true">
      {coverage.cells.map(([x, y], i) => {
        const [x0, y0] = geo.px(x - w / 2, y - h / 2);
        const [x1, y1] = geo.px(x + w / 2, y + h / 2);
        return <rect key={i} x={Math.min(x0, x1)} y={Math.min(y0, y1)} width={Math.abs(x1 - x0)} height={Math.abs(y1 - y0)} fill={OUTAGE} opacity="0.05" />;
      })}
    </g>
  );
});

// Measured gusts (Met Office UKV), shaded from 60 km/h.
const GustField = memo(function GustField({weather, geo, t}) {
  const [w, h] = weather.cell;
  const gusts = gustsAt(weather, t);
  return (
    <g aria-hidden="true">
      {weather.nodes.map(([x, y], i) => {
        const g = gusts[i];
        if (g == null || g < 60) return null;
        const [x0, y0] = geo.px(x - w / 2, y - h / 2);
        const [x1, y1] = geo.px(x + w / 2, y + h / 2);
        return (
          <rect
            key={i}
            x={Math.min(x0, x1)}
            y={Math.min(y0, y1)}
            width={Math.abs(x1 - x0)}
            height={Math.abs(y1 - y0)}
            fill={g >= 110 ? '#e6f1f8' : g >= 90 ? '#a9c8de' : '#6f93ad'}
            opacity={Math.min(0.5, (g - 50) / 120)}
          />
        );
      })}
    </g>
  );
});

// Real power cuts appear as they started; recent ones are brighter.
const Outages = memo(function Outages({outages, times, geo, t, s=1}) {
  return (
    <g aria-hidden="true">
      {outages.map((o, i) => {
        const age = t - times[i];
        if (age < 0) return null;
        const [x, y] = geo.px(o[0], o[1]);
        const fresh = age < 1;
        return <circle key={i} cx={x} cy={y} r={(fresh ? 2.2 : 1.3) * s} fill={OUTAGE} opacity={fresh ? 0.95 : 0.35} />;
      })}
    </g>
  );
});
