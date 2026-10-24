# Predictions → preparedness → decisions

## Two audiences

Residents and pedestrians receive general outage preparedness guidance and links to
Power Cut 105 and Met Office warnings. These actions do not alter network failure
rates. The benchmark cannot infer an individual home's supply connection or provide
safe walking routes. Asset intervention is for qualified network organisations.

Organisation/government planning has two time horizons:

- Pre-storm: compare a prioritized repair queue against the original branch-ID queue.
  The existing crew count, hourly repair rules and weather remain the same. Priority
  is a heuristic: descending single-asset hardening benefit measured on training
  simulations. This is not an optimized routing or crew-location model. Geographic
  pre-positioning needs a real network, depots, road access and travel times.
- Long-term: choose line/transformer hardening, assumed installed before the storm.
  Use the suggestion or choose any affordable set. Apply plan & rerun same storm
  creates a custom portfolio and keeps the completed run's weather, random draws,
  repair durations and median no-action playback example. Scenario controls are
  reset to that completed run so a changed storm cannot masquerade as mitigation.

Hardening still uses assumed hazard and capacity multipliers, including when failure
probabilities come from an external ML model. No asset-specific intervention efficacy
has been learned. Transformer/line classification follows the documented case14
export order (15 lines then 5 transformers), not a generic inference from tap ratio.

## Cost analysis

Editable assumptions default to £10,000 per budget unit, £1,000 per unserved MWh and
one equivalent storm event. Individual asset GBP costs can be overridden in the asset table; blank uses the unit-based default. These are demonstration inputs, not market quotes,
regulated interruption costs or estimated storm frequencies. The optimiser still
maximizes training energy-loss reduction under the units budget; it does not claim
to maximize net monetary benefit.

For a portfolio, using paired held-out mean MWh avoided:

```
investment = sum(asset_costs_gbp override, or asset units × cost_per_unit_gbp)
avoided loss per event = MWh avoided × outage_value_gbp_per_mwh
benefit = avoided loss per event × expected_events
net benefit = benefit − investment
benefit/cost = benefit / investment
break-even events = investment / avoided loss per event
```

Ratios are null for zero investment; break-even is null without positive benefit
and positive investment. Negative gains/benefits remain negative. The equivalent
event count is an undiscounted investment-horizon scenario assumption. No annual
probability, asset lifetime, maintenance savings, repair costs, travel costs, market
price effects or geographic/customer damage model is inferred. Repair queue
ordering has zero *reinforcement* spend; dispatch and staffing costs are excluded.

The asset table uses **training-only, single-asset** benefits, not held-out outcomes.
These effects cannot be added because reinforcement changes flows and cascades.
The portfolio table and monetary summary evaluate the combined intervention.
The optimization trace records conditional training gains as each asset is added.
Repeated manual tuning against the same evaluation bank is exploratory; change
seed or use a separately reserved evaluation event before reporting final results.

## What uncertainty is shown

- Peak and final-hour major-outage probabilities (>50% of demand shed).
- Mean hours with any settled load shed; fraction fully served at the horizon.
- Worst-decile mean energy loss (largest ceil(10% × N) outcomes).
- Paired mean gain and approximate normal 95% Monte Carlo confidence interval.
- Fraction of scenarios improved and worsened.

Intervals quantify finite simulation sampling under the supplied conditions. They
do not include ML/model calibration errors, forecast uncertainty or financial
assumption uncertainty. Recovery hour is null when supply is still interrupted at
the horizon; otherwise it records the first hourly state after the last outage,
or zero for an uninterrupted scenario. Failed assets can still exist when all load
is served. Recovery is measured for demand, not completion of every asset repair.

## Demo storms and observed data

Three multi-day windstorm presets are provided: single peak, persistent strong
winds and two successive fronts. They are synthetic exposures for stress tests,
not different calibrated meteorological hazards. Seeded failures and repair durations
are reproducible. Imported historical weather overrides the synthetic preset and
reference gust. No real storm dataset has been bundled or silently downloaded.

Generic scenarios are useful for demonstrating robustness. Historical storm weather
is useful for replay. Neither supplies observed asset-failure labels by itself.
Train ML on asset-hour exposure records with observed failures and operating
non-events, then hold entire storms out of fitting and tuning. Synthetic failure
labels can test plumbing but do not establish real-world predictive skill.
