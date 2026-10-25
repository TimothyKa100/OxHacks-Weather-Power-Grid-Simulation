import React, {memo, useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {mapFrame, outline} from './geo.js';
import {flashesAt, gustsAt, layerTime, mix, stateAt, stormAt} from './timeline.js';
import {fetchAssets} from './api.js';

const LINE = {400: '#a9bccb', 275: '#8399aa', 132: '#617888'};
const WIDTH = {400: 1.5, 275: 1.1, 132: 0.75};
const DOWN = {weather: '#e5484d', pylon: '#e5484d', overload: '#f2994a', transient: '#e8c547'};
const BUS = {powered: '#c9d6e2', dark: '#e5484d'};
const OUTAGE = '#ffd27a';
const lineColor = (status, kv) => (status === 'online' ? LINE[kv] || LINE[275] : DOWN[status] || DOWN.pylon);
const isDark = (frame, i, load) => frame.node_status
  ? ['out_of_supply','reduced_supply','deenergized'].includes(frame.node_status[i])
  : load > 0 && frame.served_by_bus[i] < load - 0.5;
const STATUS_TEXT = {weather: 'tower damage (crew repair)', online: 'in service', pylon: 'pylon down', overload: 'tripped on overload (remote reclose)',
                     transient: 'locked out after failed auto-reclose (remote reclose)'};
const KIND_TEXT = {line: 'overhead line', cable: 'underground cable', transformer: 'transformer', link: 'inferred link'};
// Cables and inferred links are drawn dashed/dotted when in service; transformers are thin site connectors.
const KIND_DASH = {cable: [2.2, 1.6], link: [0.6, 1.8]};
const KIND_WIDTH = {transformer: 0.6};
const quantize = (x, step) => Math.round(x / step) * step;
const MAX_ZOOM = 12;
const DRAG_PX = 4;

// Everything is drawn from the simulation clock t, not CSS transitions, so the
// map always shows exactly the state at time t.
// Corridor outage risk: log scale from 1e-4 to 0.3 (per hour, or per storm for totals).
const RISK_LOW = '#3a2d5c';
const RISK_HIGH = '#d08cff';
export const riskLevel = p => Math.min(1, Math.max(0, (Math.log10(Math.max(p, 1e-4)) + 4) / (4 + Math.log10(0.3))));

export default function UkMap({network, run, timeline, t, hardened=[], selected, onSelect, risk, onView, focus, cascade, lineNotes}) {
  const geo = useMemo(() => mapFrame(network.buses), [network]);
  const shapes = useMemo(() => {
    const index = new Map(network.buses.map((b, i) => [b.id, i]));
    const buses = network.buses.map(b => geo.px(b.x, b.y));
    const routes = network.branches.map(e => {
      // Real route where the backend has one, with its ends pinned to the substations. In a view
      // network a bus can stand for a cluster far from where the route ends; then keep the route's
      // own ends instead of drawing a long straight spoke to the cluster's bus.
      const ends = [buses[index.get(e.from)], buses[index.get(e.to)]];
      const route = (e.geometry || []).map(([x, y]) => geo.px(x, y));
      if (route.length < 2) {
        const points = ends;
        return {points: points.map(p => p.map(v => v.toFixed(1)).join(',')).join(' '), mid: points[0], raw: points};
      }
      const gap = (p, q) => Math.hypot(p[0] - q[0], p[1] - q[1]);
      const flipped = gap(route[0], ends[1]) + gap(route[route.length - 1], ends[0]) < gap(route[0], ends[0]) + gap(route[route.length - 1], ends[1]);
      const path = flipped ? [...route].reverse() : route;
      const near = 6 * outline.scale / 111;  // ~6 km in screen units (degrees x scale)
      const first = gap(path[0], ends[0]) < near ? ends[0] : path[0];
      const last = gap(path[path.length - 1], ends[1]) < near ? ends[1] : path[path.length - 1];
      const points = [first, ...path.slice(1, -1), last];
      return {points: points.map(p => p.map(v => v.toFixed(1)).join(',')).join(' '), mid: points[Math.floor(points.length / 2)], raw: points};
    });
    return {buses, routes};
  }, [network, geo]);

  const state = timeline ? stateAt(timeline, t) : null;
  const fadeStep = state ? quantize(state.fade, 0.1) : 1;
  const storm = timeline ? stormAt(timeline, t) : null;
  const {wrapRef, svgRef, box, view, zoomBy, reset, center, animateTo, fit} = useZoom(outline.width, outline.height);
  useFocus(focus, network, shapes.routes, center, animateTo, fit);
  const assets = useViewAssets(geo, view.box, network.id || 'gb', onView);
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
      {risk && <g opacity={cascade ? 0.25 : 1}><CorridorRisk network={network} routes={shapes.routes} risk={risk} s={s} /></g>}
      <Lines network={network} routes={shapes.routes} state={state} fadeStep={fadeStep} geo={geo} hardened={hardened} selected={selected} onSelect={onSelect} notes={lineNotes} s={s} />
      {cascade && <CascadeTrips network={network} routes={shapes.routes} ids={cascade.tripped} t={t} s={s} />}
      {assets && <Assets data={assets} geo={geo} network={network} state={state} fadeStep={fadeStep} s={s} />}
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
      {cascade && <div className="cascade-banner" role="status">Cascade · hour {cascade.hour} · {cascade.label}</div>}
      <div className="zoom-controls">
        <button type="button" onClick={() => zoomBy(2)} disabled={view.k >= MAX_ZOOM} aria-label="Zoom in">+</button>
        <button type="button" onClick={() => zoomBy(0.5)} disabled={view.k <= 1} aria-label="Zoom out">−</button>
        <button type="button" onClick={reset} disabled={view.k <= 1} aria-label="Reset zoom">⟲</button>
      </div>
    </div>
    </div>
  );
}

// The backend picks the ~1,000 most important archive assets in whatever is on screen
// (no preset zoom levels); refetch once the view settles, cancelling stale requests.
function useViewAssets(geo, viewBox, networkId, onView) {
  const [assets, setAssets] = useState(null);
  const key = viewBox.map(v => v.toFixed(2)).join(',');
  useEffect(() => {
    const [px, py, w, h] = viewBox;
    const [x0, y0] = geo.unpx(px, py);
    const [x1, y1] = geo.unpx(px + w, py + h);
    const controller = new AbortController();
    const box = [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
    const timer = setTimeout(() => {
      onView?.(box);
      fetchAssets(box, 1000, controller.signal, networkId).then(data => data && setAssets(data)).catch(() => {});
    }, 150);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [geo, key, networkId]);  // eslint-disable-line react-hooks/exhaustive-deps
  return assets;
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

  // Glide to a view (used to focus on a cascade and to return afterwards).
  const timer = useRef(null);
  const centerNow = useRef(center);
  centerNow.current = center;
  const animateTo = useCallback((target, ms = 700) => {
    clearInterval(timer.current);
    const startedAt = performance.now();
    const from = centerNow.current, to = clamp(target);
    timer.current = setInterval(() => {
      const u = Math.min(1, (performance.now() - startedAt) / ms), e = u * u * (3 - 2 * u);
      setCenter(clamp({k: Math.exp(Math.log(from.k) + (Math.log(to.k) - Math.log(from.k)) * e),
                       cx: from.cx + (to.cx - from.cx) * e, cy: from.cy + (to.cy - from.cy) * e}));
      if (u >= 1) clearInterval(timer.current);
    }, 1000 / 30);
  }, [clamp]);
  useEffect(() => () => clearInterval(timer.current), []);
  // The view that shows a box (svg units) with some margin, no closer than FOCUS_ZOOM.
  const fit = useCallback(([x0, y0, x1, y1]) => {
    const k = Math.min(FOCUS_ZOOM, base[0] / Math.max(1e-6, (x1 - x0) * 1.6), base[1] / Math.max(1e-6, (y1 - y0) * 1.6));
    return {k: Math.max(1, k), cx: (x0 + x1) / 2, cy: (y0 + y1) / 2};
  }, [base]);

  return {wrapRef, svgRef, box, view, zoomBy, reset, center, animateTo, fit};
}

const FOCUS_ZOOM = 6;

// While `focus` lists branches (a cascade), glide to them; afterwards glide back to the earlier view.
function useFocus(focus, network, routes, center, animateTo, fit) {
  const saved = useRef(null);
  const key = focus?.length ? focus.join(',') : '';
  const centerRef = useRef(center);
  centerRef.current = center;
  useEffect(() => {
    if (!key) {
      if (saved.current) { animateTo(saved.current); saved.current = null; }
      return;
    }
    const index = new Map(network.branches.map((e, i) => [e.id, i]));
    const points = focus.flatMap(id => routes[index.get(id)]?.raw || []);
    if (!points.length) return;
    const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
    if (!saved.current) saved.current = centerRef.current;
    animateTo(fit([Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]));
  }, [key]);  // eslint-disable-line react-hooks/exhaustive-deps
}

// Lines tripped by the cascade so far: a pulsing halo so each protection round stands out.
function CascadeTrips({network, routes, ids, t, s = 1}) {
  const index = new Map(network.branches.map((e, i) => [e.id, i]));
  const pulse = 0.55 + 0.45 * Math.sin(t * 2 * Math.PI / 0.04);
  return (
    <g aria-hidden="true" pointerEvents="none">
      {ids.map(id => index.has(id) && (
        <polyline key={id} points={routes[index.get(id)].points} fill="none" stroke={DOWN.overload}
          strokeOpacity={0.35 + 0.4 * pulse} strokeWidth={(4 + 3 * pulse) * s} strokeLinecap="round" strokeLinejoin="round" />
      ))}
    </g>
  );
}

// Lines and buses only change at an hourly event and during its short fade.
const Lines = memo(function Lines({network, routes, state, geo, hardened, selected, onSelect, notes, s=1}) {
  return (
    <g>
      {network.branches.map((e, i) => {
        const status = state?.frame.status[i] ?? 'online';
        const before = state?.previous.status[i] ?? status;
        const down = status !== 'online';
        const inService = state?.frame.circuits_in_service?.[i];
        const partial = !down && inService !== undefined && e.circuits && inService < e.circuits;
        const dash = down ? [3, 2.5] : KIND_DASH[e.kind];
        const fallen = status === 'pylon' ? state.frame.fallen_at?.[i] : null;
        const at = fallen ? geo.px(fallen[0], fallen[1]) : null;
        return (
          <g key={e.id} role="button" tabIndex={0} aria-label={`Inspect ${e.id}`} onClick={()=>onSelect?.(e.id)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();onSelect?.(e.id)}}}>
            <polyline
              points={routes[i].points}
              fill="none"
              stroke={selected===e.id?'#fff':status==='online'&&hardened?.includes(e.id)?'#a8dc81':mix(lineColor(before, e.kv), lineColor(status, e.kv), state ? state.fade : 1)}
              strokeWidth={((KIND_WIDTH[e.kind] ?? WIDTH[e.kv] ?? 1)+(hardened?.includes(e.id)?1:0)+(selected===e.id?1:0)) * s}
              strokeOpacity={partial ? 0.55 : 1}
              strokeDasharray={dash ? `${dash[0] * s} ${dash[1] * s}` : undefined}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <title>
                {e.id} · {e.name || KIND_TEXT[e.kind] || 'Transmission branch'}
                {e.kv ? ` · ${e.kind === 'transformer' && e.kv_low ? `${e.kv}/${e.kv_low}` : e.kv} kV` : ''}
                {e.kind && e.kind !== 'line' && e.name ? ` · ${KIND_TEXT[e.kind]}` : ''} · {STATUS_TEXT[status] || status}
                {partial ? ` · ${inService} of ${e.circuits} circuits in service` : ''}
                {notes?.[i] ? ` · ${notes[i]}` : ''}
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
}, (a, b) => a.network === b.network && a.routes === b.routes && a.state?.i === b.state?.i && a.fadeStep === b.fadeStep && a.hardened === b.hardened && a.selected === b.selected && a.onSelect === b.onSelect && a.notes === b.notes && a.s === b.s);

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

// Grid-archive assets: glyph by kind, size by importance. An asset goes red when the simulation
// leaves its supplying backbone bus short of supply.
const ASSET_STYLE = {
  generation: {color: '#7bd88f', shape: 'circle', label: 'Power station'},
  substation: {color: '#9fb3c8', shape: 'square', label: 'Substation'},
  transformer: {color: '#c9a9e8', shape: 'diamond', label: 'Transformer'},
  storage: {color: '#e0b05c', shape: 'square', label: 'Storage'},
  converter: {color: '#5fc3e4', shape: 'diamond', label: 'HVDC converter'},
  compensator: {color: '#e0b05c', shape: 'diamond', label: 'Compensator'},
  switchgear: {color: '#9fb3c8', shape: 'diamond', label: 'Switchgear'},
  distributed_generator: {color: '#4f8f63', shape: 'circle', label: 'Embedded generator'},
};
const Assets = memo(function Assets({data, geo, network, state, s=1}) {
  return (
    <g className="assets">
      {data.assets.map(([x, y, kind, score, kv, mw, bus, name, id]) => {
        const style = ASSET_STYLE[data.kinds[kind]] || ASSET_STYLE.substation;
        const [cx, cy] = geo.px(x, y);
        const r = (0.5 + 0.4 * score) * s;
        const dark = state && bus >= 0 && bus < network.buses.length && isDark(state.frame, bus, network.buses[bus].load_mw);
        const fill = dark ? BUS.dark : style.color;
        const title = `${name || style.label}${kv ? ` · ${kv} kV` : ''}${mw ? ` · ${mw} MW` : ''} · ${style.label}` +
          (bus >= 0 ? ` · supplied via bus ${bus + 1}${dark ? ' (losing supply)' : ''}` : ' · not simulated (Northern Ireland)');
        const common = {fill, fillOpacity: 0.85, stroke: '#0d0f12', strokeWidth: 0.25 * s};
        const shape = style.shape === 'circle' ? <circle cx={cx} cy={cy} r={r} {...common} />
          : style.shape === 'square' ? <rect x={cx - r} y={cy - r} width={2 * r} height={2 * r} {...common} />
          : <path d={`M${cx},${cy - r * 1.3}L${cx + r * 1.3},${cy}L${cx},${cy + r * 1.3}L${cx - r * 1.3},${cy}Z`} {...common} />;
        return <g key={id}>{shape}<title>{title}</title></g>;
      })}
    </g>
  );
}, (a, b) => a.data === b.data && a.geo === b.geo && a.state?.i === b.state?.i && a.fadeStep === b.fadeStep && a.s === b.s);

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
