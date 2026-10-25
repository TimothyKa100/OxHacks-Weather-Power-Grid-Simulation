import React, {memo, useId} from 'react';
import {checkStorm, STORM_LIMITS, STORM_PRESETS} from './config.js';
import './storm-scenario.css';

// Controlled component: no app, map, router or request dependencies.
function StormControls({value, onChange, placement, onPlacementChange, message, disabled = false}) {
  const id = useId();
  const update = (key, next) => onChange({...value, [key]: next});
  const problem = checkStorm(value);
  const numeric = (key, label, step = 1) => <label key={key}>
    {label}<input type="number" min={STORM_LIMITS[key][0]} max={STORM_LIMITS[key][1]} step={step}
      value={value[key]} onChange={event => update(key, event.target.value === '' ? '' : Number(event.target.value))}/>
  </label>;
  return <fieldset className="storm-editor" disabled={disabled}>
    <legend>Create a storm</legend>
    <div className="storm-editor__row">
      <label htmlFor={`${id}-preset`}>Storm type<select id={`${id}-preset`} aria-label="Storm type" value={value.preset} onChange={event => update('preset', event.target.value)}>
        {Object.entries(STORM_PRESETS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select></label>
      <button type="button" aria-pressed={placement === 'origin'} onClick={() => onPlacementChange(placement === 'origin' ? null : 'origin')}>
        {placement === 'origin' ? 'Cancel placement' : 'Place in ocean'}
      </button>
      <button type="button" aria-pressed={placement === 'direction'} onClick={() => onPlacementChange(placement === 'direction' ? null : 'direction')}>Set direction</button>
    </div>
    <p className="storm-editor__hint" role="status">{message}</p>
    <div className="storm-editor__fields">
      {numeric('peak_gust_kmh', 'Peak gust (km/h)')}{numeric('radius_km', 'Core radius (km)')}
      {numeric('speed_kmh', 'Travel speed (km/h)')}{numeric('bearing_deg', 'Direction (° clockwise from north)')}
      {numeric('hours', 'Duration (hours)')}{numeric('initial_soil_moisture', 'Initial soil wetness (m³/m³)', .01)}
    </div>
    <details><summary>Starting conditions</summary><div className="storm-editor__fields">
      <label>Start longitude<input type="number" min="-25" max="15" step="0.1" value={value.origin.lon} onChange={e => update('origin', {...value.origin, lon: e.target.value === '' ? '' : Number(e.target.value)})}/></label>
      <label>Start latitude<input type="number" min="45" max="65" step="0.1" value={value.origin.lat} onChange={e => update('origin', {...value.origin, lat: e.target.value === '' ? '' : Number(e.target.value)})}/></label>
      {numeric('seed', 'Random seed')}
      <label>Season / start hour (UTC)<input type="datetime-local" step="3600" value={value.start_time.slice(0, 16)} onChange={e => update('start_time', `${e.target.value}:00+00:00`)}/></label>
    </div></details>
    {problem && <p className="storm-editor__error" role="alert">{problem}</p>}
    <p className="storm-editor__hint">Wind, gusts, direction, rain, temperature and soil wetness evolve together. Grid demand stays at its fixed average. Synthetic scenario.</p>
  </fieldset>;
}

export default memo(StormControls);
