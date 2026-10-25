# Hybrid integration and sensitivity results

## ML as a power-cut forecast (current)

The corridor model now has its own output: a forecast of local (distribution) power cuts near each
overhead route, scored against reported cuts for historical storms (`backend/forecast.py`). The
unit is the model's target: a line-hour with at least one reported cut within 5 km. Only routes
inside the outage feed's coverage, and hours the model scores, are compared.

Storm Darragh on the full GB network (archived complete-feature v2 predictions, NGED feed):

| Measure | Value |
|---|---|
| Routes compared | 79 |
| Forecast corridor-hours with a cut | 197 |
| Reported | 680 (3.4×) |
| Hourly timing correlation | 0.70 |
| Peak hour | 26 forecast, 24 reported |
| Reported corridor-hours on the top-10% forecast routes | 24% |

The timing is right, but the count is about 3.4 times too low. The model learned SSEN's northern
Scotland, and NGED's network is denser. The UI uses whatever network is on screen, and on view
networks the ML runs on the reduced (gusts-only) model.

The transmission cascade no longer uses the ML by default: `failure_mode` defaults to `engineering`.
The bounded hybrid below stays available as `failure_mode: "hybrid"`, an experiment.

## Storm chain

`POST /api/storm-plan` (and `python scripts/run_chain.py`) runs one chain:

1. weather;
2. the ML power-cut forecast (separate output, above);
3. the engineering route/pylon hazard (optionally tilted by the experimental hybrid: β = 0.5,
   multiplier 0.5–2, hourly expected failures preserved);
4. Monte Carlo line damage and transient faults;
5. the cascade: redispatch, trips, islanding and supply loss;
6. every mitigation on the same draws: weather-risk and power-flow baselines, the suggested
   reinforcement, prioritized repairs, and **reinforcement plus prioritized repairs**;
7. post-storm restoration (48 calm hours by default, `recovery_hours`, 4 h steps; crews and the
   repair queue act, with no new weather damage);
8. cost-benefit;
9. a recommendation.

When the forecast cannot run (for example, imported weather without route features), the chain
records why and continues. The result's `chain` lists each stage.
The recommendation is the plan with the highest net benefit among plans whose held-out paired
gain has a 95% interval above zero; otherwise it is no action.

Example (`run_chain.py`; synthetic 190 km/h, 12 h, seed 7, 16 scenarios, budget 3, 6 crews,
48 h restoration; about 84 s):

| Plan | Lines | Paired gain, MWh | Net benefit |
|---|---|---:|---:|
| Weather-risk baseline | L222, L235, L078 | -587 | -£0.62m |
| Suggested reinforcement | L064, L138 | 6,714 | £6.68m |
| Prioritized repairs | — | 5,508 | £5.51m |
| **Reinforcement + prioritized repairs** (recommended) | L064, L138 | **9,558** (95% 945 to 18,171) | **£9.53m** |

Repairs have a median of four days, so few finish within 48 h, and no scenario is fully
restored by the horizon. Restoration steps are 4 h long: a repair that completes during a step
is credited from the step's start, and a crew that finishes mid-step waits for the next step.

### Engineering vs hybrid decisions on matched draws

`python scripts/compare_hybrid_decisions.py` gives
[hybrid_decisions_v2.json](results/hybrid_decisions_v2.json): storm hours only, without the
restoration tail. Each side's chosen lines are re-run under both hazards on the same seed.

- **Synthetic 190 km/h, 12 h.** v2 runs on the reduced model: synthetic weather has gusts only.
  - It tilts 435 overhead lines within the full 0.5–2 range.
  - It changes both choices. The risk ranking picks L078 instead of L224. The suggested
    reinforcement picks L140 + L138 instead of L216 + L140.
  - The engineering choice L216 + L140 scored better **under both hazards**: 10,359 against
    11,600 MWh under the hybrid hazard. So the hybrid's own greedy search did worse on the held-out draws.
  - With 24 training and 16 held-out scenarios, the selection is noisy, and this run does not
    show the ML improving decisions.
- **Storm Darragh replay (60 h).** v2 runs on archived complete ERA5 features.
  - With the 265 km/h fragility median, Darragh breaks almost nothing (0.06 lines on average).
  - Both hazards choose the same plans.

The ML now decides which lines break: it moves probability between overhead lines, and the
cascade engine draws damage from the tilted probabilities. Whether that improves decisions
needs larger scenario banks and real transmission outcomes, neither of which exists here.

## Earlier built-in hybrid (model v1)


The default hybrid now uses the **actual saved corridor XGBoost**, without retraining
or a reference-model upload. It redistributes engineering risk using same-hour
weather/engineering peers, preserves expected hourly line failures and reports
geographic/target transfer and missing inputs. This supersedes the optional
synthetic-pair experiment below as the default UI behavior.

Reproduce with `python scripts/benchmark_builtin_hybrid.py`. Machine-readable
results: [builtin_hybrid.json](results/builtin_hybrid.json).

Fixed **synthetic** 190 km/h stress test, 12 h, seed 7, 16 held-out failure/repair
draws, six crews, budget three, median pylon gust 265 km/h and beta .5:

| No-action metric | Engineering | Built-in hybrid |
|---|---:|---:|
| Mean unserved energy, MWh | 27,601.317 | 25,212.950 |
| Mean distinct failed branches | 47.375 | 46.625 |
| Mean overload trip events | 15.438 | 14.688 |
| Mean nodes ever losing all supply | 1.4375 | 1.1250 |
| Mean nodes ever impaired | 21.4375 | 22.1875 |

The chosen median-loss hybrid playback has up to **41 weather-damaged lines,
13 overload-tripped lines, one node without supply and twelve with reduced supply**.
Counts are maxima of settled frames and need not occur in the same hour. Nodes
lose service through connectivity/capacity; no physical substation-damage model
has been invented. An explicit repair test confirms supply returns after repairs.

There are 3,830 changed line-hours in this case. The largest absolute difference
between engineering and hybrid expected one-hour failure counts is 3.56e-15.
Actual distinct failures can change because line availability, repair and cascades
depend on which assets break. The lower energy loss is **not improved predictive
accuracy**: neither trajectory is an observed transmission outcome. The increase
in nodes with any impairment also shows why a single outcome metric is insufficient.

Hybrid's suggested portfolio is L175/L026, with mean loss 24,244.075 MWh under its
own assumptions. Prioritized repairs alone worsen mean loss to 26,835.457 MWh in
this case; the heuristic is not guaranteed to improve outcomes.

Synthetic weather has raw route gusts and partial history; rain, temperature,
crosswind and initial histories remain missing and follow tree missing-value paths.
This inference is unvalidated. Darragh instead reuses the shipped complete-feature
ERA5 predictions. Severe demo weather is labelled synthetic; the engineering curve
was not weakened or probabilities floored to force a historical replay to fail.

## Earlier optional synthetic-pair experiment

The remaining results use **synthetic paired scores**, not a newly trained transmission model. The network and route-weather engine are the actual GB prototype. No unpublished training dataset was accessed.

## Reproducible experiment

GB graph: 303 buses / 367 branches. Fixed synthetic storm: seed 7, 190 km/h reference gust, 12 hours. Six repair crews. Each case has 16 Monte Carlo scenarios; failure seeds 7, 26 and 42. Fragility medians: 250, 265, 300 km/h. Beta: 0, 0.5, 1. Bounds: [0.5, 2]. No reinforcement in this sweep. Weather, failure draws and repair draws stay matched across comparisons.

The reference fixture is an analytic gust curve; its full score adds an arbitrary branch-index sinusoid in log odds. It is deliberately not labelled XGBoost or observed asset condition. It proves the residual is wired into the sampler; it does not evaluate ML accuracy.

| Assumed median gust | Beta | Mean loss, engineering MWh | Mean loss, hybrid MWh | Mean distinct failed lines, hybrid |
|---:|---:|---:|---:|---:|
| 250 | 0 | 39003.7 | 39003.7 | 67.10 |
| 250 | 0.5 | 39003.7 | 39458.9 | 65.96 |
| 250 | 1 | 39003.7 | 39225.4 | 65.98 |
| 265 | 0 | 26708.8 | 26708.8 | 48.38 |
| 265 | 0.5 | 26708.8 | 27471.8 | 47.65 |
| 265 | 1 | 26708.8 | 27213.1 | 47.67 |
| 300 | 0 | 2468.8 | 2468.8 | 9.46 |
| 300 | 0.5 | 2468.8 | 4167.3 | 12.06 |
| 300 | 1 | 2468.8 | 4883.6 | 14.02 |

Values average the three seeds; seeds sharing a physical scenario are not independent weather events. The JSON contains per-seed paired uncertainty, not an empirical forecasting interval.

All beta-zero comparisons reproduce engineering losses exactly. Increasing beta can increase or decrease loss because residuals can move either direction and network interactions are nonlinear. The fragility median materially changes the engineering baseline. Neither beta nor the median is calibrated to transmission observations here.

## Full mitigation integration example

For the same fixed storm and seed 7, beta 0.5, median 265, and budget 3: engineering no-action mean loss was 27601.3 MWh; hybrid no-action mean loss was 25060.8 MWh.
Engineering selected ['L001', 'L175']; hybrid selected ['L194', 'L182', 'L207']. These are independently optimised plans under assumed hazards, not proof that either protects the real grid.

## Paired learning results

Automated tests fit, calibrate, save, reload and export both models on explicitly synthetic asset-hour labels, and check leakage/evidence gates. This verifies implementation only. The stored corridor-v1 evaluation remains the earlier area-target result. There is no empirical result for the proposed transmission target and no fitted reference for the legacy v1 artifact.

When the pending data arrives, train a new immutable paired version, export its full/reference scores, and repeat the sensitivity and mitigation evaluation on reserved storms and regions. Beta/bounds and engineering fragility choices must be frozen using development data before scoring the final holdout.

Reproduce: `python scripts/benchmark_hybrid.py`. Machine-readable results: [sweep](results/hybrid_sensitivity.json), [decision example](results/hybrid_decision_example.json).
