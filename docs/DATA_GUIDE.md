> Current land filtering, zoom hierarchy, pylon heights and expanded outage sources are documented in [UK_DATA_AND_OUTAGES.md](UK_DATA_AND_OUTAGES.md). The counts and SSEN-only scope below describe the earlier source/research stage.

The real-GB collection contains source curation and an hourly feeder research pipeline; no empirical failure model has been trained. The separate IEEE benchmark has an illustrative DC cascade simulator. See `FAILURE_PIPELINE.md` for generated features, evidence gates, training/evaluation commands and the explicit probability importer.

**The collection is not yet a verified supervised circuit-failure training dataset.** Public source limitations prevent that claim. `unplanned_incidents_VERIFIED.parquet` is deliberately empty; `fault_candidates_UNVERIFIED.parquet` is a quarantined research table, not approved labels.

## Scope and source access

The pasted notes mention an existing postcode outage dataset, but no such data file was present in the attachments or repository. It was therefore not used.

The working region is SSEN: southern England (SEPD) and northern Scotland (SHEPD). This is not a complete UK network. The operator catalogue, resource descriptions, schemas, publication dates, raw files and SHA-256 download manifests are saved locally.

- [SSEN NaFIRS HV faults](https://data.ssen.co.uk/dataset/nafirs-hv-faults): 76,574 records, 1 April 2015–18 October 2026. CC BY 4.0. Published incident timestamps have minute resolution. The schema describes reported incident time; onset versus reporting delay requires confirmation.
- [SSEN open data catalogue](https://data-api.ssen.co.uk/api/3/action/package_search?rows=1000): overhead routes, substations, supply hierarchies, LTDS electrical models and workbooks, generation registers and capacity context. `data/manifest.json` provides exact resource URLs and licences. The catalogue and manifests are the authoritative source index.
- [ENW historical unplanned outages](https://electricitynorthwest.opendatasoft.com/explore/dataset/unplanned-outages/): catalogue accessible, records returned HTTP 403. Its current schema does not expose the circuit field described in the older workbook guide. No ENW outage records were downloaded.
- [UKPN LTDS circuit data](https://ukpowernetworks.opendatasoft.com/explore/dataset/ukpn-ltds-table-1-circuit-data/): catalogue accessible, records returned HTTP 403. No UKPN circuit records were downloaded. UKPN EHV Network Outages is a planning dataset and was not used as fault labels.
- [Open-Meteo historical weather](https://open-meteo.com/en/docs/historical-weather-api): ERA5/Copernicus via Open-Meteo, hourly, UTC, wind in km/h; approximately 0.25-degree native spatial grid. Requests here sample a 0.5-degree regional lattice around overhead-circuit representative locations. This is a coarse weather association, not route-resolved wind measurement. Requests stop on hourly/daily quotas and can resume. See `weather_manifest.json` and the final coverage report for actual downloads.
- [Forest Research open data](https://www.forestresearch.gov.uk/tools-and-resources/fthr/open-data/): NFI GB 2024 woodland polygons, queried around the two regions from the Forestry Commission ArcGIS service. Service metadata is retained. Geometry generalised to 5 m; derived exposure uses a 30 m buffer. This measures proximity to mapped woodland land, including NFI categories such as clearfell/restock, not actual tree height or current canopy. Preserve publisher attribution and consult saved metadata before redistribution.

## Planned outages: strict exclusion, not guessing

SSEN's description and methodology explicitly say the data contains planned and unplanned outages. Neither CSV contains a planned/unplanned field. Cause and equipment codes are not a reliable substitute.

6,990 records with operational/safety restrictions, generator removal/temporary connection restoration, testing/commissioning or explicit planned wording were excluded from the candidate table. These are **possible operational/planned interruptions**, not 6,990 confirmed planned events. The remaining 69,584 also have unverified planning status. All have `training_eligible=false`. No uncertain event is admitted to `unplanned_incidents_VERIFIED.parquet`. Original source files are preserved for audit; excluded audit output contains identifiers and reasons only.

To resolve this, obtain an explicit incident classification from SSEN or a documented guarantee that the particular export excludes planned events. Then join it by operator, reporting year, district and incident reference; inspect duplicate IDs before accepting any join. Never label an incident unplanned merely because its description does not contain the word “planned”.

## Main tables

All paths below are under `data/processed/`. Parquet is used to preserve identifiers, nulls and timestamp types. `schema_inventory.json` documents every output's schema and row count.

| Table | Meaning and limitations |
|---|---|
| `feature_snapshot.parquet` | Example assembled feeder features at a single UTC timestamp. Only lagged weather features are joined. All rows remain `training_ready=false`; no target or at-risk state is invented. Rebuild with `scripts/feature_snapshot.py --at <UTC timestamp>`. |
| `circuit_roster.parquet` | 4,439 feeders from published primary/feeder/secondary supply-area hierarchy. Includes feeders without mapped overhead routes. Coverage is the published supply-area inventory, not proof that every historical circuit is represented. |
| `circuits.parquet` | 2,956 GIS circuit IDs associated with 139,799 overhead segments. Known overhead length, nominal voltage where consistent, representative coordinates, woodland exposure when computed; explicit nulls for unavailable properties. Not a complete all-cable circuit roster. |
| `overhead_segments.parquet` | Individual overhead routes, BNG/WGS84 geometry, voltage, source ID, physical route length. Coordinates are EPSG:27700 for lengths and EPSG:4326 for geographic positions. |
| `supply_hierarchy.parquet` | Published circuit-to-secondary-area-to-primary/GSP relationships. A supply hierarchy is not switch-level electrical connectivity. |
| `fault_candidates_UNVERIFIED.parquet` | Unapproved incident candidates, minute timestamps, cause, damage and affected-customer attributes. Cause, damaged equipment, duration and affected customers are **post-event labels/analysis fields, not predictor inputs**. |
| `unplanned_incidents_VERIFIED.parquet` | Strict model label output: currently zero rows. |
| `substations_existing.parquet` | 121,929 rows with source status `Existing`, retaining type, class, situation and coordinates. `Existing` does not establish energised/operational status. `substations_all_statuses` also preserves abandoned/removed rows. |
| `electrical_branches_SEPD/SHEPD.parquet` | May 2026 LTDS branches, endpoint buses, voltage, positive-sequence R/X/B, seasonal MVA ratings and lengths. 100 MVA base is printed in the source sheets. |
| `electrical_lines_SEPD/SHEPD.parquet` | CIM ACLineSegments with resistance/reactance in ohms, susceptance in siemens, voltage in kV, length in km and terminal node IDs. Fault-roster crosswalk is missing. |
| `transformer_windings_*`, `equipment_seasonal_ratings_*`, `electrical_generators_*` | Typed winding voltages/ratings/impedances, seasonal limits resolved through published terminal IDs to equipment, and generator maximum net power. Limits preserve A versus MVA; no operating dispatch is inferred. |
| `electrical_terminals_*` and `cim_*` | Electrical equipment-to-node incidence, switch normal-open state, transformer windings, generators, limits and connectivity. All terminal node references resolve within each model. This checks references, not power-flow validity. |
| `cim_properties_*` | Lossless property/reference tables from the existing-network EQ and SYSCAP profiles. Includes the properties not promoted to dedicated tables. |
| `ltds_workbooks/` | Archived workbook sheets staged as Parquet cells, with original multi-row headers retained. Sheet indexes in `sheets.json`; these are staging tables, not automatically typed model features. |
| `connected_generation_snapshots.parquet` | Connected generation only, historical register snapshots, capacities and connection dates where published. Snapshot title/source identifies each version. Never backfill current generation into earlier years; missing connection dates remain unknown. |
| `bressay_demand_native.parquet` | Native 30-minute demand records, meter counts where available, original timestamps and explicit unit uncertainty. Companion documentation says Wh was converted to kWh by multiplying by two, which is dimensionally inconsistent. Values are retained as reported; no trusted MW load is inferred. |
| `weather/*.parquet` | Hourly meteorology and lagged exposure features, one partition per weather request location. |
| `circuit_weather_crosswalk.parquet` | GIS circuit to coarse weather request location. Availability must be checked against downloaded partitions. |
| `roster_weather_crosswalk.parquet` | Feeder-roster association to downloaded weather, with distance and availability. Spatial association is approximate. |
| `woodland_2024.parquet` | Regional NFI geometry, EPSG:27700 WKB, categories and source feature IDs. |
| `segment_woodland_exposure.parquet` | Segment length within 30 m of NFI woodland geometry. |

The NRN crosswalk removes separators and normalises leading zeros: e.g. `0545.301.000.00` and `545_301` become primary 545, feeder 301. About 85% of candidate incidents match the feeder roster. This is a **candidate structural crosswalk**, not an operator-certified identifier match. Ambiguous overhead matches are not assigned. No nearest-substation matching is used to infer failed equipment. GIS operator classification uses geography only as a clearly marked heuristic.

Electrical branches are edges between electrical buses. A feeder can be a node in your ML/consequence graph, but that representation does not remove the need for bus/terminal/branch connectivity in electrical maths. The feeder inventory and electrical model remain separate until a verified crosswalk is obtained. Circuit rows contain null impedance/rating fields rather than borrowing unrelated values.

## Time, thresholds and missing data

“Lowest time resolution” has been interpreted as the **shortest available interval**. Incident minutes and hourly weather are preserved without inventing sub-hour weather. LTDS and generation inventories are snapshots, not time-series measurements. No scaler is fitted; numerical scaling/imputation must be fitted on the training split only.

Incident timestamps have no published timezone. Original local timestamps are preserved; a clearly labelled provisional Europe/London-to-UTC conversion is supplied. Ambiguous/nonexistent DST times become null, not silently guessed.

Exposure thresholds are exploratory feature cutoffs, **not established equipment damage limits**:

- Gusts at least 80, 100 and 120 km/h.
- Air temperature at least 30°C or at most −5°C.
- Precipitation at least 10 mm in an hour; this is heavy-rain exposure, not flood depth.

For each threshold, 6/24/72-hour lagged exceedance counts and a 24-hour excess-intensity sum are supplied. Wind maxima and rainfall totals are also lagged. At timestamp `t`, these derived features only use weather samples strictly before `t`. Full windows are required: missing samples and insufficient history yield null, never zero risk.

Hourly gust samples do not measure exact minutes above a threshold. The field names say `sample_hours` to distinguish this proxy from measured continuous exposure. Short gusts and local terrain effects can be missed. ERA5 is retrospective reanalysis: these data support historical conditional-risk replay; genuine advance forecasting requires archived forecasts and their issue times.

No true failure-free circuit-hour table is manufactured. Absent fault reports do not establish negative labels until reporting completeness and circuit availability are known. The export gives average customer time off supply, not final circuit restoration; dividing customer minutes by customers does not solve this. Unknown downtime, planned maintenance, commissioning/retirement and missing reporting must be censored.

## Before training, in priority order

1. Obtain planned/unplanned status, confirmed onset and restoration times, incident deduplication rules, timezone and reporting-coverage intervals. This unlocks reliable labels and at-risk negative exposure.
2. Confirm the NRN crosswalk and obtain a complete historical circuit roster, including cables and decommissioned/reconfigured circuits. Obtain the GIS/NaFIRS-to-LTDS/CIM crosswalk; do not guess it from names or proximity.
3. Resolve weather coverage gaps, then sample weather along entire routes at the native grid and validate severe gusts against station observations. Use finer observations where available; interpolation is not new information.
4. Add overhead/underground total lengths, pole/conductor types, commissioning/replacement dates, inspection/condition scores, vegetation clearance and repair records. Current NFI and network snapshots are not historical ground truth. Add terrain and woodland height/condition when available.
5. Add lightning observations and historical inundation depth or river-level exposure only when spatial/temporal coverage aligns. Snow depth and precipitation are not icing load and flood depth. These missing hazards remain null.
6. For cascading maths, obtain historical switch states, bus loads, generation dispatch, protection settings, thermal ratings and their time dependence, transformer taps and confirmed line parameters. Validate intact power flow first. Static published seasonal capacity is not observed operational loading. Zero-impedance LTDS entries are flagged: they require topology/bus processing before any inverse-impedance calculation.
7. Split chronologically by whole storms, with spatial holdouts and quiet periods. Fit imputers/scalers on training only. Evaluate calibration, Brier/log loss, rare-event precision–recall and storm-wide uncertainty; compare wind-only and historical-rate baselines. Evaluate previously-failed versus never-failed circuits separately.
8. Use incident `DAMAGE` and cause to distinguish direct physical damage, transient trips, upstream loss of supply and operational interventions. Circuit interruption probability is not automatically component breakage probability.

## Your proposed probability mechanism

Accumulated extreme exposure is a sensible input to a conditional failure-hazard model, alongside current/lagged weather, asset properties, maintenance and operating state. A fixed rule “probability above 50% means break” is usually unsuitable for stochastic failure simulation: it suppresses low-probability failures and creates artificial synchronisation. Later, sample an operating circuit's failure with `u < p_t`, where `u` is uniform on [0,1), and run many trajectories. A probability threshold can separately define a warning or intervention.

Match probability to the time interval. For a constant hazard rate λ, `p(Δt) = 1 - exp(-λ Δt)`. Do not apply an hourly probability every minute. Shared weather creates correlated exposure; residual storm-level dependence may still require modelling. Keep physically damaged, tripped, undamaged-but-unsupplied and restored states distinct. Only initiate an electrical cascade from correctly mapped direct failures; propagating already-downstream interruption labels would double count effects.

The downloaded electrical data is useful groundwork, but the missing feeder crosswalk, operational states and validated operating point prevent claiming a calibrated overload-cascade-ready dataset today.
