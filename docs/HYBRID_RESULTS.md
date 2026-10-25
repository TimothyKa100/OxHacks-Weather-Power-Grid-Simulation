# Hybrid integration and sensitivity results

All results below use **synthetic paired scores**, not a newly trained transmission model. The network and route-weather engine are the actual GB prototype. No unpublished training dataset was accessed.

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
