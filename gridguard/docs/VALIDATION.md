# Completed validation

Validated locally on 24 October 2026 using Windows, Python 3.13.5, Node 24.14.0, Flask 3.1.3, NumPy 2.4.6, pandapower 3.5.6, pytest 9.1.1, React 18.3.1 and Vite 7.3.7. Exact installed Python packages are recorded in `requirements-tested-lock.txt`; npm uses `frontend/package-lock.json`.

## Automated checks

`python -m pytest -q`: **14 passed**.

The checks cover:

- 14 benchmark buses, 20 branches and 259 MW total demand.
- Original external-grid active capacity of 332.4 MW preserved during export (pandapower's solved internal ppc otherwise replaces the limit with a large sentinel).
- Intact branch flows agree with pandapower's DC result to 1e-7 MW.
- Active-power conservation, isolated generator-free loads, and insufficient 140 MW island generation.
- No-failure operation retains full load with zero unserved energy.
- A forced line outage causes actual overload trips and recomputed power flows.
- Cascades terminate, loads remain disconnected, settled final branch loadings respect assumed ratings, and hourly unserved-energy accounting is consistent.
- Monotonic illustrative fragility with wind and the exact hardening factor.
- Deterministic replay, unique affordable investments, zero-budget handling and finite comparison metrics.
- API health, successful experiment responses and rejection of malformed objects, non-integer/bool parameters and out-of-range settings.

`npm run build`: **passed**, producing the bundled Flask-served frontend. Runtime Python imports do not require pandapower; the checked-in benchmark export and its independent reference flows are included. Pandapower is required for re-exporting the benchmark.

## Browser checks

The dashboard was opened against the actual Flask server at `http://127.0.0.1:5001`, rather than mocked API data. Verified default results and a changed-seed 64-scenario run, no-action/reinforced/side-by-side views, hour scrubbing, automatic playback from hour 0 to hour 5 with automatic stop, line and bus inspection, strategy switching and the methodology dialog. The exported JSON was downloaded and parsed, confirming seed 26, 128 scenarios and all four portfolios. Responsive layouts at 1440px and 390px widths were inspected; document widths remained within their viewports. Screenshots are real captures of the running app:

- `dashboard.jpg`: default seeded run and reinforcement plan.
- `comparison.jpg`: the shared example storm before and after reinforcement.
- `mobile.jpg`: narrow viewport layout.

The example run chooses L04, L07, L01, L02. Expected unserved energy is 830.049 MWh without action and 497.222 MWh under that selection; the power-flow baseline ties it. The weather-risk baseline returns 674.800 MWh. With seed 27 and 64 held-out storms, the power-flow baseline outperforms GridGuard's training-selected portfolio; the UI reports it without overriding selection or altering outcomes.

## Scope of evidence

The electrical reference check validates the intact **DC** model. It does not validate AC contingency behavior, transient stability, real relay timing, the invented fragility, artificial weather correlations or assumed thermal ratings. Browser checks are local functional/visual checks, not a cross-browser accessibility certification. No external forecast or live GDELT request was made. Installation from the release's dependency manifests still needs package-registry access.
