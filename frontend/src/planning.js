// A view adapter only: physics, repairs and decisions come from the backend.
export const portfolioNames = {
  none: 'No action', risk: 'Weather-risk baseline', topology: 'Power-flow baseline',
  optimized: 'Suggested reinforcement', prepared: 'Prioritized repairs', custom: 'Your reinforcement plan',
};

export function portfolioRun(run, key) {
  const portfolio = run?.plan?.portfolios[key];
  if (!portfolio) return run;
  const settled = new Map(portfolio.playback.frames.map(frame => [frame.hour, frame]));
  const background = new Map(run.frames.map(frame => [frame.hour, frame]));
  const frames = [...settled.values()].sort((a, b) => a.hour - b.hour).map(frame => ({
    ...frame, events: [], fallen_at: Array(frame.status.length).fill(null),
    storm: background.get(frame.hour)?.storm ?? null,
    storm_radius: background.get(frame.hour)?.storm_radius ?? 0,
    line_failure_probability: frame.failure_probability, islands: frame.islands.length,
  }));
  return {...run, frames};
}

export function sameStormRequest(run, customLines, economics) {
  if (!run?.plan) throw new Error('Run a decision comparison first.');
  return {...run.replay.request, custom_lines: [...customLines], economics};
}
