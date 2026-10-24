import React, {memo, useMemo} from 'react';
import {mapFrame, outline} from './geo.js';
import {flashesAt, gustsAt, layerTime, mix, stateAt, stormAt} from './timeline.js';

const LINE = {400: '#a9bccb', 275: '#8399aa', 132: '#617888'};
const WIDTH = {400: 1.5, 275: 1.1, 132: 0.75};
const DOWN = {pylon: '#e5484d', overload: '#f2994a'};
const BUS = {powered: '#c9d6e2', dark: '#e5484d'};
const OUTAGE = '#ffd27a';
const lineColor = (status, kv) => (status === 'online' ? LINE[kv] || LINE[275] : DOWN[status] || DOWN.pylon);
const isDark = (frame, i, load) => load > 0 && frame.served_by_bus[i] < load - 0.5;
const STATUS_TEXT = {online: 'in service', pylon: 'pylon down', overload: 'tripped on overload'};
const quantize = (x, step) => Math.round(x / step) * step;

// Everything is drawn from the simulation clock t, not CSS transitions, so the
// map always shows exactly the state at time t.
export default function UkMap({network, run, timeline, t}) {
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

  return (
    <svg className="uk-map" viewBox={`0 0 ${outline.width} ${outline.height}`} role="img" aria-label="Map of the GB transmission grid">
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
      <Lines network={network} routes={shapes.routes} state={state} fadeStep={fadeStep} geo={geo} />
      <Buses network={network} positions={shapes.buses} state={state} fadeStep={fadeStep} />
      {run?.outages && timeline && <Outages outages={run.outages} times={timeline.outageTimes} geo={geo} t={layerTime(t)} />}
      {timeline &&
        flashesAt(timeline, t).map(f => {
          const [x, y] = f.at ? geo.px(f.at[0], f.at[1]) : shapes.routes[f.branch].mid;
          return (
            <circle
              key={f.branch}
              cx={x}
              cy={y}
              r={2 + 16 * f.progress}
              fill="none"
              stroke={DOWN[f.status] || DOWN.pylon}
              strokeWidth="1.5"
              opacity={1 - f.progress}
              pointerEvents="none"
            />
          );
        })}
    </svg>
  );
}

// Lines and buses only change at an hourly event and during its short fade.
const Lines = memo(function Lines({network, routes, state, geo}) {
  return (
    <g>
      {network.branches.map((e, i) => {
        const status = state?.frame.status[i] ?? 'online';
        const before = state?.previous.status[i] ?? status;
        const down = status !== 'online';
        const fallen = status === 'pylon' ? state.frame.fallen_at?.[i] : null;
        const at = fallen ? geo.px(fallen[0], fallen[1]) : null;
        return (
          <g key={e.id}>
            <polyline
              points={routes[i].points}
              fill="none"
              stroke={mix(lineColor(before, e.kv), lineColor(status, e.kv), state ? state.fade : 1)}
              strokeWidth={WIDTH[e.kv] || 1}
              strokeDasharray={down ? '3 2.5' : undefined}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <title>
                {e.name || e.id}
                {e.kv ? ` · ${e.kv} kV` : ''} · {STATUS_TEXT[status] || status}
              </title>
            </polyline>
            {at && (
              <path
                d={`M${at[0] - 3},${at[1] - 3}L${at[0] + 3},${at[1] + 3}M${at[0] + 3},${at[1] - 3}L${at[0] - 3},${at[1] + 3}`}
                stroke={DOWN.pylon}
                strokeWidth="1.6"
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
}, (a, b) => a.network === b.network && a.routes === b.routes && a.state?.i === b.state?.i && a.fadeStep === b.fadeStep);

const Buses = memo(function Buses({network, positions, state}) {
  return (
    <g>
      {network.buses.map((b, i) => {
        const served = state ? state.frame.served_by_bus[i] : b.load_mw;
        const darkNow = state ? isDark(state.frame, i, b.load_mw) : false;
        const darkBefore = state ? isDark(state.previous, i, b.load_mw) : false;
        const fill = mix(darkBefore ? BUS.dark : BUS.powered, darkNow ? BUS.dark : BUS.powered, state ? state.fade : 1);
        return (
          <circle key={b.id} cx={positions[i][0]} cy={positions[i][1]} r={Math.max(1.2, Math.min(3.2, Math.sqrt(b.load_mw) / 9))} fill={fill} className="bus">
            <title>
              Substation {b.id}
              {b.kv ? ` · ${b.kv} kV` : ''}: {darkNow ? 'losing supply' : 'supplied'}
            </title>
          </circle>
        );
      })}
    </g>
  );
}, (a, b) => a.network === b.network && a.positions === b.positions && a.state?.i === b.state?.i && a.fadeStep === b.fadeStep);

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
const Outages = memo(function Outages({outages, times, geo, t}) {
  return (
    <g aria-hidden="true">
      {outages.map((o, i) => {
        const age = t - times[i];
        if (age < 0) return null;
        const [x, y] = geo.px(o[0], o[1]);
        const fresh = age < 1;
        return <circle key={i} cx={x} cy={y} r={fresh ? 2.2 : 1.3} fill={OUTAGE} opacity={fresh ? 0.95 : 0.35} />;
      })}
    </g>
  );
});
