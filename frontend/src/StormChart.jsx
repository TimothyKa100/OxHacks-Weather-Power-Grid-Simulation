import React, {useLayoutEffect, useRef, useState} from 'react';

const H = 150;
const M = {top: 10, right: 10, bottom: 24, left: 40};
const ACTUAL = '#ffd27a';
const PREDICTED = '#5da8bc';

const hourLabel = iso =>
  new Date(iso).toLocaleString('en-GB', {weekday: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC'});
// Predicted collapses per hour can be tiny (1e-6), so keep significant figures.
const small = v => (v === 0 ? '0' : v >= 0.01 ? v.toFixed(2) : v.toExponential(1));

function useWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(480);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const update = () => setWidth(Math.max(240, Math.floor(el.clientWidth)));
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

// Actual vs predicted breakage per hour for a real storm, on one axis. Real power
// cuts number in the hundreds while predicted pylon collapses are fractions, so
// each series is scaled to its own peak: the chart compares timing, not size.
// Exact values are in the hover readout.
export default function StormChart({benchmark, timestamps, t}) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const actual = benchmark.observed_per_hour;
  const predicted = benchmark.modelled_per_hour;
  const n = actual.length;
  const maxActual = Math.max(1, ...actual);
  const maxPredicted = Math.max(...predicted) || 1;
  const innerW = width - M.left - M.right;
  const innerH = H - M.top - M.bottom;
  const band = innerW / n;
  const x = hour => M.left + hour * band; // hour boundaries; hour i spans [i, i + 1)
  const y = share => M.top + innerH * (1 - share);
  const line = predicted.map((v, i) => `${(x(i) + band / 2).toFixed(1)},${y(v / maxPredicted).toFixed(1)}`).join(' ');
  const labelEvery = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(innerW / 90))));
  const now = Math.min(n, Math.max(0, t));

  function onMove(event) {
    const rect = event.currentTarget.getBoundingClientRect();
    const hour = Math.floor((event.clientX - rect.left - M.left) / band);
    setHover(hour >= 0 && hour < n ? hour : null);
  }

  return (
    <figure className="storm-chart" ref={ref}>
      <figcaption className="chart-legend">
        <span>
          <i className="key bars" /> Actual: real power cuts per hour <small>(peak {maxActual.toLocaleString('en-GB')})</small>
        </span>
        <span>
          <i className="key line" /> Engineering wind-risk proxy per hour <small>(peak {small(maxPredicted)})</small>
        </span>
      </figcaption>
      <svg
        width={width}
        height={H}
        role="img"
        aria-label={`Observed power cuts and engineering wind risk per hour, each as a share of its own peak. Actual peaked in hour ${benchmark.peak_hour_observed}, predicted in hour ${benchmark.peak_hour_modelled}.`}
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        {[0, 0.5, 1].map(share => (
          <g key={share}>
            <line x1={M.left} x2={M.left + innerW} y1={y(share)} y2={y(share)} className={share === 0 ? 'axis' : 'gridline'} />
            <text x={M.left - 6} y={y(share) + 4} textAnchor="end" className="tick">
              {share * 100}%
            </text>
          </g>
        ))}
        <text x={10} y={M.top + innerH / 2} transform={`rotate(-90 10 ${M.top + innerH / 2})`} textAnchor="middle" className="tick">
          of peak
        </text>
        {actual.map((v, i) => (
          <rect
            key={i}
            x={x(i) + 0.5}
            y={y(v / maxActual)}
            width={Math.max(0.5, band - 1)}
            height={innerH * (v / maxActual)}
            fill={ACTUAL}
            opacity={i < now ? 0.85 : 0.3}
          />
        ))}
        <polyline points={line} fill="none" stroke={PREDICTED} strokeWidth="2" strokeLinejoin="round" />
        {timestamps.map((iso, i) =>
          i % labelEvery === 0 ? (
            <text key={i} x={x(i)} y={H - 6} textAnchor="start" className="tick">
              {hourLabel(iso)}
            </text>
          ) : null,
        )}
        <line x1={x(now)} x2={x(now)} y1={M.top} y2={M.top + innerH} className="playhead" />
        {hover !== null && <rect x={x(hover)} y={M.top} width={band} height={innerH} className="hover-band" />}
      </svg>
      {hover !== null && (
        <div className="chart-tip" style={{left: Math.min(width - 190, Math.max(0, x(hover) + band + 6))}}>
          <b>{hourLabel(timestamps[hover])} UTC</b>
          <span>
            <i className="key bars" /> {actual[hover].toLocaleString('en-GB')} real power cuts
          </span>
          <span>
            <i className="key line" /> {small(predicted[hover])} modelled risk
          </span>
        </div>
      )}
    </figure>
  );
}
