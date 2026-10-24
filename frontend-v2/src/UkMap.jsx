import React from 'react';
import {busPositions, isGeographic, outline, stormAt} from './geo.js';
import {TAIL, flashesAt, mix, stateAt} from './timeline.js';

const LINE = {online: '#9fb4c4', weather: '#e5484d', overload: '#f2994a'};
const BUS = {powered: '#f0efec', dark: '#e5484d'};
const lineColor = status => LINE[status] || LINE.weather;
const isDark = (frame, i, load) => load > 0 && frame.served_by_bus[i] < load - 0.01;

// Every colour change is driven by the simulation clock t (not CSS
// transitions), so the map always shows exactly the state at time t.
export default function UkMap({network, timeline, t, weather}) {
  const {buses, branches} = network;
  const pos = busPositions(buses);
  const index = new Map(buses.map((b, i) => [b.id, i]));
  const state = timeline ? stateAt(timeline, t) : null;
  const flashes = timeline ? flashesAt(timeline, t) : new Map();
  const storm = timeline ? stormAt(weather?.windows, t, isGeographic(buses), timeline.lastHour, TAIL) : null;

  return (
    <svg className="uk-map" viewBox={`0 0 ${outline.width} ${outline.height}`} role="img" aria-label="Map of the UK power grid">
      <defs>
        <radialGradient id="storm">
          <stop offset="0" stopColor="#8fb3d9" stopOpacity="0.5" />
          <stop offset="0.5" stopColor="#8fb3d9" stopOpacity="0.2" />
          <stop offset="1" stopColor="#8fb3d9" stopOpacity="0" />
        </radialGradient>
      </defs>
      <path d={outline.path} className="land" />
      {storm && (
        <circle
          className="storm"
          cx={storm.x}
          cy={storm.y}
          r={70 + 60 * storm.strength}
          fill="url(#storm)"
          opacity={storm.alpha}
        />
      )}

      {branches.map((e, i) => {
        const [x1, y1] = pos[index.get(e.from)];
        const [x2, y2] = pos[index.get(e.to)];
        const status = state?.frame.status[i] ?? 'online';
        const before = state?.previous.status[i] ?? status;
        const down = status !== 'online';
        const color = mix(lineColor(before), lineColor(status), state ? state.fade : 1);
        const flash = flashes.get(i);
        return (
          <g key={e.id}>
            <line
              x1={x1}
              y1={y1}
              x2={x2}
              y2={y2}
              stroke={color}
              strokeWidth={down ? 2 : 2.5}
              strokeDasharray={down ? '5 4' : undefined}
              strokeLinecap="round"
            >
              <title>
                {e.id}: bus {e.from} to {e.to}
                {down ? ` (tripped by ${status})` : ''}
              </title>
            </line>
            {flash && (
              <circle
                cx={(x1 + x2) / 2}
                cy={(y1 + y2) / 2}
                r={4 + 22 * flash.progress}
                fill="none"
                stroke={lineColor(flash.status)}
                strokeWidth="2"
                opacity={1 - flash.progress}
                pointerEvents="none"
              />
            )}
          </g>
        );
      })}

      {buses.map((b, i) => {
        const served = state ? state.frame.served_by_bus[i] : b.load_mw;
        const darkNow = state ? isDark(state.frame, i, b.load_mw) : false;
        const darkBefore = state ? isDark(state.previous, i, b.load_mw) : false;
        const fill = mix(darkBefore ? BUS.dark : BUS.powered, darkNow ? BUS.dark : BUS.powered, state ? state.fade : 1);
        return (
          <circle key={b.id} cx={pos[i][0]} cy={pos[i][1]} r="5.5" className="bus" fill={fill}>
            <title>
              Bus {b.id}
              {b.name ? ` (${b.name})` : ''}: {served.toFixed(1)} of {b.load_mw.toFixed(1)} MW supplied
            </title>
          </circle>
        );
      })}
    </svg>
  );
}
