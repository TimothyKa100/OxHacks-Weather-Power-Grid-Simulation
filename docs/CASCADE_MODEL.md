# GB cascade model

This revision answers the cascade review: the old model was a reasonable teaching model of how
failures spread, but its errors pushed results in predictable directions. Each point is listed
below with what changed and what is still an assumption. The IEEE 14-bus benchmark keeps the
original engine; everything here applies to the GB map (`backend/grid_ops.py`,
`scripts/build_gb_network.py`).

## Network topology (review points 1, 2, 7, 9)

The network is rebuilt from the same uk-grid-atlas OSM extract, plus the grid archive's
underground cables (`python scripts/build_gb_network.py data/grid/raw/uk_grid_atlas data/grid/uk_power_grid.sqlite`):

| Change | Why |
|---|---|
| Endpoints cluster into one bus **per voltage level** at a site; co-located levels are joined by **transformers** (137) | Clustering ignored voltage, so 132 kV routes ran in parallel with the supergrid at zero impedance. |
| Routes are **split at T-joins** where another route ends within 1 km of their interior (42) | 2 km endpoint clustering missed real joins. |
| **275/400 kV underground cables** from the archive's OSM layer (51 kept). 132 kV cables are left out | The atlas has only overhead lines; London's supply depends on cables. London's 132 kV networks run split from separate supply points, so a closed 132 kV mesh would invent parallel paths. |
| Ways between the same buses merge only if they run together (mean offset ≤ 0.25 km); **separate routes stay separate branches** | One tower collapse should not take out a separate route (point 7). |
| Overhead routes carry **two circuits** (GB L-series towers); one circuit can fault while the other carries on | Single-circuit faults no longer remove a whole route. |
| Dead ends link to a same-voltage substation within 10 km (49 inferred links); remaining dead ends get a **boundary supply** for half their demand (53) | Unmapped continuations; edges of coverage stand in for the unmodelled network. |
| Operational **interconnectors** import at their landing substation | Interconnector capacity was missing. |
| Generators snap within **20 km** (was 40) | Distant, embedded generation was propping up islands (point 3). |
| City demand goes to the nearest **supergrid** (275/400 kV) substations, in proportion to the cities' share of GB population (55%); the rest is spread over all substations | Demand landed on 132 kV cable buses, and London took 25% of GB demand (real: about 14%). |
| **No rating uplift.** Ratings are voltage class × circuits. Transformer units (unknown) and cable circuits (OSM tags incomplete) are sized so each carries ≤ 85% at four secured operating points (4 units, 3 circuits added) | The 1.6× uplift guaranteed 37% headroom and suppressed cascades (point 2). |

Point 9: the atlas contains only `power=line`, so no underground cable was ever counted as
pylons. The added cables have no towers, no wind exposure and no transient-fault rate.

### Single-circuit (N-1) security

`python scripts/security_analysis.py` (same engine rules for both):

| Measure | Before | After |
|---|---:|---:|
| Buses / branches | 303 / 367 | 478 / 673 |
| Dead-end substations | 132 | 53, all with boundary supplies |
| Ratings raised above voltage class | 29 | 0 |
| Intact maximum loading (30 GW, calm) | 77% (with uplift) | 63% |
| Single-circuit outages that split the grid | 159 | 1 |
| …that shed load | 108 | 1 |
| …that overload a line / still overloaded after re-dispatch | 15 / 5 | 2 / 0 |

GB's Security and Quality of Supply Standard requires surviving any single circuit loss, and
the rebuilt network does so in all but one case.

## Hourly operation in a storm (points 3–6)

| Step | Model | Previous model |
|---|---|---|
| Demand | Winter weekday curve: 26 GW at 04:00, 44 GW at 18:00 UTC | Fixed 30 GW |
| Wind | Turbine power curve on local wind (10 m gust ÷ 1.4 × 1.25 to hub height): cut-in 3, rated 12, **cut-out 25 m/s** (~100 km/h gust) | 30–40% output in any storm |
| Interconnectors | Imports at 80% of capacity | None |
| Preventive dispatch | Operators re-dispatch before faults so lines carry ≤ 85% (a stand-in for N-1 security). The linear program minimises the worst overload, then total overload, then MW moved | None |
| Tower damage | Engineering route/pylon probability per branch, all circuits out; **crews in gust < 60 km/h; repair lognormal, median 4 days, 1–21 days** | Median 8 hours |
| Corridor common cause | Damage hazard × a shared lognormal factor per 0.5° corridor cell per hour (σ = 0.5, mean 1) | Independent given gust |
| Transient faults | Per circuit: 1×10⁻⁴ per 100 km per hour at 50 km/h, ×e per 12 km/h. **Auto-reclose clears 85% in seconds**; the rest lock out and are **reclosed remotely next hour** (no crew) | Not modelled |
| Protection | Lines above **1.2× short-term rating trip at once**. Between 1.0 and 1.2×, corrective re-dispatch (3 rounds); otherwise the **most overloaded trips first** (time-to-trip order) | Everything above 100% tripped together |
| Overload trips | Reclosed remotely next hour | Waited for a crew and calm weather |
| Islands | Need synchronous capacity for **30% of demand** or collapse (wind, solar, storage and interconnectors don't count); otherwise proportional shedding | Survived if capacity ≥ demand |
| Hardening | Tower fragility median ×1.25 (engineering model) and half the transient rate; **no rating change**. Imported ML probabilities fall back to hazard × 0.25 | Hazard × 0.25 and +30% rating |

All parameters are in `backend/grid_ops.PARAMS`.

### What this changes on the map

| Storm (16 held-out scenarios, seed as shown) | Unserved energy | Lines failed | Overload trips | Notes |
|---|---:|---:|---:|---|
| Storm Darragh replay (seed 26) | 0 MWh | 0.06 | 0 | 74 transient faults: 58 auto-reclosed, 16 locked out and reclosed next hour. The transmission grid rode through Darragh; distribution faults are outside this model. |
| Synthetic 190 km/h, 36 h (seed 1) | 47,007 MWh | 41.7 | 1.0 | Damage plus island collapse; no repairs complete within 36 h. Suggested reinforcement (L219, L159, L084) cuts it by 23%. |

Losses are now driven by tower damage and island collapse, not by intact-grid overloads or by
islands that survive on distant wind.

## Optional AC check

`python scripts/ac_check.py --hour 18` solves a secured DC operating point as an AC power flow
in pandapower, using typical R/X and line charging per branch kind:

| Case | Result |
|---|---|
| Night trough (04:00, 26 GW) | Converges; 1.01–1.09 pu; 16 buses above 1.05 pu (cable charging at light load); losses 1.1% |
| Evening peak (18:00, 44 GW) | Converges **only without generator reactive limits**: about 8.9 GVAr short; then 0.98–1.02 pu, losses 1.9% |

The model has no static reactive compensation (SVCs, shunt capacitors and reactors), which the
real grid depends on. The DC cascade cannot see voltage collapse, so heavily stressed hours,
especially after faults at peak, may be optimistic. Adding compensation data and an AC check
after each cascade step is the next step for point 4.

## Model uncertainty (point 10's second half)

`python scripts/sensitivity_sweep.py` reruns one storm plan (synthetic 190 km/h, 36 h, seed 1,
16 held-out scenarios, budget 5) changing one assumption at a time. It uses fresh worker processes
and the same draws for each setting. The baseline Monte Carlo standard error is about 2,800 MWh.

| Setting | Unserved energy (MWh) | Lines failed | Suggested plan | Plan ranking (best first) |
|---|---:|---:|---|---|
| baseline | 47,007 | 41.4 | L219, L084, L159 | optimized > risk > none |
| fragility median 230 km/h | 98,651 | 68.6 | L107, L104, L130, L136 | optimized > risk > none |
| fragility median 300 km/h | 3,572 | 18.6 | L129, L084, L239, L222 | risk > optimized > none |
| ratings x0.9 | 47,007 | 41.6 | L219, L159, L130 | optimized > risk > none |
| ratings x1.1 | 47,007 | 41.4 | L219, L084, L159 (same) | optimized > risk > none |
| tower repair median 2 days | 47,007 | 41.4 | L219, L084, L159 (same) | optimized > risk > none |
| tower repair median 8 days | 47,007 | 41.4 | L219, L084, L159 (same) | optimized > risk > none |
| short-term rating 1.1x | 47,007 | 41.6 | L219, L084, L159 (same) | optimized > risk > none |
| short-term rating 1.3x | 47,007 | 41.4 | L219, L084, L159 (same) | optimized > risk > none |
| no corridor common cause | 50,699 | 42.9 | L219, L249 | optimized > risk > none |
| strong corridor common cause | 43,467 | 39.7 | L219, L159, L084 | risk > optimized > none |
| transient fault rate x0.5 | 46,670 | 40.2 | L219, L084, L159 (same) | optimized > risk > none |
| transient fault rate x2 | 47,196 | 42.8 | L219, L084, L159 (same) | optimized > risk > none |

- **The fragility median dominates.** ±13% around 265 km/h changes losses by a factor of 2 down
  to 1/13, and changes which lines are worth reinforcing. Calibrating it against transmission
  damage records matters more than any other parameter here.
- Ratings, short-term rating and tower repair time **do not change losses in this storm**. Loss
  comes from damaged routes isolating demand and from island collapse, not from overloads. No
  tower is repaired within the 36 h window (repairs take at least a day, and crews wait for
  safe weather), so repair time matters only for longer horizons (up to 72 h).
- Corridor common cause and transient rates move losses by under 10%, but can change which plan
  ranks first.
- The ranking of plans is unchanged in 10 of 12 settings; the suggested lines in 7 of 12.

Results: `data/sensitivity/synthetic-190.json` (local).

## Still assumptions, not calibrated

- **Failure calibration (point 8).** Tower fragility (median 265 km/h), transient rates and
  auto-reclose success are not fitted to transmission data. The right data is NESO/ENA
  transmission circuit trips, fault and repair records, and Ofgem storm reports, not
  distribution outages. The distribution-trained corridor model (GB_LINE_MODEL.md) stays a
  separate map layer.
- Two circuits per tower route, cable and transformer sizing, ratings, reactances, the demand
  map and capacity factors are illustrative.
- Preventive security is a pre-fault margin, not a full hourly N-1 contingency analysis.
  Operators' demand control, switching and emergency ratings beyond 1.2× are not modelled.
- Internal HVDC links (Western Link, Caithness–Moray, Shetland) are not modelled.
- The measure is transmission energy not served. Customer outages on distribution networks,
  the headline effect of real storms, are outside the model (point 10).
