# Multi-day storm replay prototype

The dashboard defaults to a 48-hour synthetic storm followed by recovery. Choose 24,
48 or 72 hours and 1–10 crews. The original five-hour experiment remains available.
Multi-day comparisons support 16–128 held-out scenarios through the API (64/128 in
UI). All strategies use the same weather and random failure/repair draws; selection
uses a separate 24-scenario training bank. Long runs may take longer than the demo.

## Historical exposures

Use Import hourly weather JSON in the sidebar. Up to 72 consecutive hourly records
are accepted. Timestamps must include a timezone. Gust is km/h, rainfall is mm/h.
Each value can be a scalar applied to all branches or an array in the branch order
returned by `/api/network` (L01–L20). Example:

```json
{"source":"Your dataset, event, version and mapping method", "hours":[
  {"timestamp":"2026-01-01T00:00:00Z","gust_kmh":80,"rain_mm_h":12},
  {"timestamp":"2026-01-01T01:00:00Z","gust_kmh":95,"rain_mm_h":18}
]}
```

This is a schema example, not observed weather. The chosen horizon is overridden
by the imported record count. The gust slider applies only to synthetic weather.
Source metadata and timestamps are preserved in exported runs. Imported weather
is fixed: Monte Carlo samples represent equipment failure/repair randomness, not
forecast uncertainty. Clear the import to return to synthetic weather.

For route-mapped CSV, use:

```bash
.venv/bin/python scripts/import_weather.py exposures.csv weather.json --source "Dataset, event and route mapping"
```

Columns: `timestamp,line_id,gust_kmh,rain_mm_h`. Supply every branch once per hour,
in chronological hourly groups. Missing, duplicate or unknown branches are rejected.

Candidate weather sources: [ERA5](https://cds.climate.copernicus.eu/),
[NOAA](https://www.ncei.noaa.gov/) and [Met Office](https://datahub.metoffice.gov.uk/).
Acquire actual line routes before sampling a weather grid along them; IEEE case14
positions are schematic, so importing observations does not make this a validated
regional grid model. No observed dataset is bundled or silently downloaded.

## Failure and recovery assumptions

The original illustrative hourly fragility p is converted to rate λ = −log(1−p).
Per-step failure probability is 1−exp(−λΔt); hardening multiplies λ by 0.25.
The simulator currently runs hourly; exported probabilities show local exposure
risk for that hour, including reinforced rates. A repaired asset can fail again.

At the beginning of each hour, available crews work on failed branches in branch-ID
order, provided local gust is below 60 km/h. Each crew repairs at most one branch
per hour; unfinished work persists and work can pause during unsafe weather.
Weather damage takes a seeded lognormal duration (median 8 hours, clipped to 2–36).
Overload trips take 2 crew-hours for inspection/reset. These are assumptions, not
estimated restoration times. This queue has no geography, travel or prioritization.

Demand reconnection is attempted each hour. Each island sheds demand as necessary;
loads remain shed during that hour's instantaneous cascade. Reconnection and repair
occur at discrete hourly boundaries. Energy integrates settled MW shed × duration.
No demand growth or daily load curve is applied yet.

`line_failure_probability` measures ever failing during the horizon, even if restored.
`failed_lines` counts distinct affected branches, `repairs` counts completions;
`status` in playback represents current availability. `blackout_probability` retains
its original meaning of >50% load shed at the final hour. Also inspect
`peak_outage_probability`, which detects a major outage at any settled hourly state,
and `mean_repairs`. Final recovery can hide earlier outages if only the final metric
is considered.

## Limits and next work

Fragility is uncalibrated. No ML is trained without asset-level incident and non-event
labels. No geographic routes, actual line length/elevation, dynamic thermal ratings,
protection delays, AC/frequency dynamics or forecast ensembles are implemented.
Next: one region, wind-only route exposures and historical incidents; evaluate
calibration and outage/recovery trajectories on storms held out in their entirety.
