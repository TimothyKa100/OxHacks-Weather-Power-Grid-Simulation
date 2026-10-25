// A view adapter only: physics, repairs and decisions come from the backend.
export const portfolioNames = {
  none: 'No action', risk: 'Weather-risk baseline', topology: 'Power-flow baseline',
  optimized: 'Suggested reinforcement', prepared: 'Prioritized repairs', combined: 'Reinforcement + prioritized repairs',
  custom: 'Your reinforcement plan',
};

// Each hour shows its settled state; an hour with a cascade also keeps every protection round
// before it (backend 'Cascade trip' frames), so playback can step through the cascade.
export function portfolioRun(run, key) {
  const portfolio = run?.plan?.portfolios[key];
  if (!portfolio) return run;
  const byHour = new Map();
  for (const frame of portfolio.playback.frames) {
    if (!byHour.has(frame.hour)) byHour.set(frame.hour, []);
    byHour.get(frame.hour).push(frame);
  }
  const background = new Map(run.frames.map(frame => [frame.hour, frame]));
  const shape = (frame, cascade) => ({
    ...frame, events: [], fallen_at: Array(frame.status.length).fill(null),
    storm: background.get(frame.hour)?.storm ?? null,
    storm_radius: background.get(frame.hour)?.storm_radius ?? 0,
    line_failure_probability: frame.failure_probability, islands: frame.islands.length,
    ...(cascade ? {cascade} : {}),
  });
  const frames = [];
  for (const hour of [...byHour.keys()].sort((a, b) => a - b)) {
    const list = byHour.get(hour);
    const rounds = list.filter(f => f.kind === 'Cascade trip');
    rounds.forEach((f, k) => frames.push(shape(f, {round: k + 1, rounds: rounds.length, tripped: f.new_trips})));
    frames.push(shape(list[list.length - 1], rounds.length ? {round: rounds.length + 1, rounds: rounds.length, settled: true} : null));
  }
  return {...run, frames};
}

export function sameStormRequest(run, customLines, economics) {
  if (!run?.plan) throw new Error('Run a decision comparison first.');
  return {...run.replay.request, custom_lines: [...customLines], economics};
}
