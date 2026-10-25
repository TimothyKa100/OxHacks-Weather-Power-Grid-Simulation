import React, {useId, useRef} from 'react';
import {checkStorm, directionTo, stormPoint} from './config.js';

// SVG-only adapter: project([lon,lat]) -> [x,y], unproject([x,y]) -> [lon,lat].
// Mount inside any SVG map. Replacing this with Leaflet requires no contract changes.
export default function StormTrackOverlay({config, project, unproject, onChange, disabled = false, scale = 1}) {
  const markerId = useId().replaceAll(':', '');
  const dragging = useRef(false);
  if (checkStorm(config)) return null;
  const start = project([config.origin.lon, config.origin.lat]);
  // A short direction handle stays reachable even when the full track leaves the map.
  const handle = project(stormPoint(config, Math.min(config.hours - 1, 250 / config.speed_kmh)));
  const end = project(stormPoint(config, config.hours - 1));
  const edge = project([config.origin.lon, config.origin.lat + config.radius_km / 111.32]);
  const radius = Math.hypot(edge[0] - start[0], edge[1] - start[1]);
  const move = event => {
    if (!dragging.current) return;
    event.stopPropagation();
    const svg = event.currentTarget.ownerSVGElement;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(svg.getScreenCTM().inverse());
    onChange(directionTo(config, unproject([point.x, point.y])));
  };
  return <g className="storm-track" aria-label="Storm track preview">
    <defs><marker id={markerId} markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7" fill="#b5dd87"/></marker></defs>
    <g pointerEvents="none">
      <circle cx={start[0]} cy={start[1]} r={radius} fill="#b5dd87" fillOpacity=".08" stroke="#b5dd87" strokeOpacity=".4" strokeDasharray={`${5 * scale} ${4 * scale}`} strokeWidth={scale}/>
      <line x1={start[0]} y1={start[1]} x2={end[0]} y2={end[1]} stroke="#b5dd87" strokeWidth={scale} strokeOpacity=".4" strokeDasharray={`${4 * scale} ${5 * scale}`}/>
      <line x1={start[0]} y1={start[1]} x2={handle[0]} y2={handle[1]} stroke="#b5dd87" strokeWidth={2 * scale} markerEnd={`url(#${markerId})`}/>
      <circle cx={start[0]} cy={start[1]} r={5 * scale} fill="#b5dd87"/>
    </g>
    {!disabled && <circle cx={handle[0]} cy={handle[1]} r={13 * scale} fill="transparent" stroke="#b5dd87" strokeWidth={scale}
      role="slider" tabIndex={0} aria-label="Storm travel direction" aria-valuemin={0} aria-valuemax={360} aria-valuenow={config.bearing_deg}
      onKeyDown={event => {
        if (['ArrowLeft', 'ArrowDown', 'ArrowRight', 'ArrowUp'].includes(event.key)) {
          event.preventDefault();event.stopPropagation();
          const delta = ['ArrowLeft', 'ArrowDown'].includes(event.key) ? -5 : 5;
          onChange({...config, bearing_deg: (config.bearing_deg + delta + 360) % 360});
        }
      }}
      onPointerDown={event => {event.preventDefault();event.stopPropagation();dragging.current = true;event.currentTarget.setPointerCapture(event.pointerId);}}
      onPointerMove={move}
      onPointerUp={event => {event.stopPropagation();dragging.current = false;event.currentTarget.releasePointerCapture(event.pointerId);}}
      onPointerCancel={() => {dragging.current = false;}}
      onClick={event => event.stopPropagation()}><title>Drag to change direction; arrow keys rotate 5°</title></circle>}
  </g>;
}
