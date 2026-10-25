# Regional failure-model and cascade data collection

The supplied CSV has been moved, without changing its bytes, to [`data/grid/raw/outages/user_supplied/unplanned_outages.csv`](../data/grid/raw/outages/user_supplied/unplanned_outages.csv). Its SHA-256 is `b8cf9c33af912589004f1bfc79abf8647c3b94563226706edfbd812e89462195`. The manifest records the original location and that the original download URL/licence were not supplied. Source labels remain evidence about origin, rather than a verified licence or lineage claim.

The current active outage register contains **197,955 source reports/observed episodes**. The import added 127,764 records, matched 51 existing episodes and excluded 70 records without usable UK-land coordinates. Raw source records remain available. One source episode can describe repeated observations of an ongoing fault; these totals are not certified unique physical failures.

| Priority area | Active regional observations | What was recovered |
|---|---:|---|
| Southern Scotland | 534 | Located SPEN archive reports. A further 382 retained SPEN reports are elsewhere, mostly northern England/north Wales; all SPEN reports are not Scottish. |
| Northern England | 3,200 | NPG, ENWL and English SPEN observations, including existing NPG live reports. The region uses these operators' English service footprints, not a new official geographic boundary. |
| Northern Ireland | 58 | Official NIE public Powercheck fault snapshot, with IDs, causes, customer counts, postcode sectors and Irish-grid coordinates. No retrospective completeness is claimed. |

The regional subset is [`priority_region_outage_observations.parquet`](../data/grid/normalized/priority_region_outage_observations.parquet). Each record retains source, time basis, location method and identity/eligibility fields. Operator/region filters work through `/api/grid/outages/summary?operator=SPEN` and `?region=Southern%20Scotland`, `Northern%20England` or `Northern%20Ireland`.

## Making use of the supplied archive

The CSV supplies 127,827 rows: NGED 82,969; UKPN 39,083; NPG 2,268; WPD 1,197; SPEN 923; SSEN 713; ENWL 674. WPD is normalised to NGED and NPg to NPG. The file itself contains no NIE rows.

As requested, integration proceeds without reconstructing collection periods. Observation bases are still preserved: a first snapshot is an observed time, not a measured failure onset; a last snapshot is not actual restoration. The map displays this distinction, and `restored_at_utc` is populated only from `reported_restored` evidence with a consistent date order. Estimated restoration stays separate. Supplied `duration_min` remains an archive field rather than silently becoming physical downtime.

References recur across archive episodes. The import keeps distinct `record_id` episodes, matching an existing report only when operator/reference and reported time or the observed interval agree. Original evidence remains in `outage_source_evidence` and [`supplemental_outage_evidence.parquet`](../data/grid/normalized/supplemental_outage_evidence.parquet), including evidence attached to merged reports. The import is idempotent. It supplies regional interruption observations and validation evidence, while `training_eligible` and `asset_failure_verified` remain false until identity and exposure are verified.

## Stronger northern England identifiers

The public [OSUKED Outage-Watch archive](https://github.com/OSUKED/Outage-Watch) preserves native Northern Powergrid feed fields that are absent from the supplied CSV. Three hundred committed snapshots were downloaded with commit times, source URLs and hashes. They yield **1,994 incident references** and 2,144 incident/object/time combinations.

Matching operator/reference and reported time within one hour recovers a **single consistent `networkObject` reference for 1,622 imported NPG reports**. The field appears as `reported_network_object_id` in the active register and inspector. The full reference table is [`npg_reported_network_objects.parquet`](../data/grid/normalized/npg_reported_network_objects.parquet).

This is a source-reported DMS object pointer. Its object class and correspondence to the physical asset inventory are not certified. It must not be relabelled a verified failed asset. A DNO crosswalk from these object IDs to GIS/LTDS/CIM IDs would turn this into much stronger circuit/asset evidence. Commit timestamps describe archive snapshots; they do not certify uninterrupted polling or exact event onset.

## Northern Ireland sources acquired

The official [NIE public Powercheck map](https://powercheck.nienetworks.co.uk/) uses a public JSON fault endpoint, now included in `download_uk_outages.py`. The feed was found through the map's published JavaScript, independent of the registration-gated open-data portal. Coordinates are converted from Irish Grid EPSG:29903 to WGS84. The source omits the year and UTC offset from some displayed times: year inference is marked, and ambiguous daylight-saving times keep a null UTC conversion. Postcode sectors remain sectors, rather than invented full postcode units. A missing incident in a later snapshot does not establish restoration.

From [SONI Outage Information](https://www.soni.ltd.uk/grid/outage-information), the collection also contains:

- **1,089 annual transmission maintenance-plan rows** across 2024–2026, naming substations, circuits, equipment, voltage and planned work dates. Eighty-four rows have an end date earlier than the start date and are explicitly flagged for review.
- **144 distinct weekly transmission schedule documents**, with 3,085 reference observations and 1,525 unique plan references. Extracted text spans retain the PDF, page and hash. They require source review before machine interpretation of equipment or dates; Gantt shading is not converted into invented execution times.
- **3,886 all-island generation schedule observations**, including 615 observations for the explicitly named NI stations Ballylumford, Kilroot and Coolkeeragh. Repeated weekly observations are versions, not independent outages. Source outage codes are retained without guessing their meanings; future end dates can be availability horizons rather than actual restoration.
- The **340-page All-Island Ten-Year Transmission Forecast Statement 2024**, containing topology/schematics, transmission characteristics, station demand forecasts, generation/dispatch assumptions, fault levels and power-flow tables. This is a copyrighted reference, not an unrestricted substitute for a machine-readable operational case. Its planning assumptions must be reconciled with the event being simulated.

Schedules remain in `soni_transmission_maintenance_plans.parquet`, `soni_weekly_outage_references.parquet` and `soni_generation_outage_schedule_observations.parquet`, separately from observed customer faults. SONI describes the transmission schedules as planned maintenance/testing/connection outages; text mentioning repairs does not prove that a new unplanned failure occurred on the scheduled date.

The [Utility Regulator's April 2026 NIE resilience consultation](https://www.uregni.gov.uk/files/uregni/documents/2026-04/NIEN%20Resilience%20licence%20mods_0.pdf), Tables 2.1–2.2, supplies useful **independent storm-impact validation targets**:

| Storm Éowyn 2025 network class | Reported faults | Customer interruptions |
|---|---:|---:|
| 33 kV | 71 | 33,371 |
| 11 kV overhead mainline | 480 | 240,112 |
| 11 kV overhead spur | 616 | 36,316 |
| 6.6 kV | 9 | 6,095 |
| LV | 1,511 | 9,467 |
| Total in Table 2.2 | 2,687 | 325,361 |

The same table reports rounded customer-minutes lost by class and a total of 514 million. The narrative separately mentions more than 5,000 faults; that is a different source population/counting scope and must not be silently equated with the 2,687 table total. Rounded component CML values do not sum exactly to the printed total. The figures are transcribed with source-page references into [`NI_Eowyn_2025_validation_targets.csv`](../data/grid/normalized/NI_Eowyn_2025_validation_targets.csv).

The published network-length table is exported as [`NI_network_lengths_validation.csv`](../data/grid/normalized/NI_network_lengths_validation.csv). These are current aggregate lengths, not historical asset-hours or automatic normal-weather failure denominators. Together, these tables can constrain an event-conditioned NI storm-impact model and check mainline-versus-spur consequences; one storm does not establish a generally calibrated asset hazard model.

## Electrical-model and validation inputs acquired

A pinned [PyPSA-GB](https://github.com/andrewlyden/PyPSA-GB) input bundle adds GB network workbooks, coordinates, demand allocation and a reduced **29-bus, 99-line, 6-link** research benchmark. Line endpoints resolve internally. The source includes ETYS and supporting network workbooks for building a larger model. The upstream current documentation describes several model resolutions; the downloaded reduced input itself has 29 buses, so 32 is not assumed from the general description.

Impedance bases/units need reconciliation before importing the benchmark into a solver: upstream source documentation describes per-unit R/X/B, while modelling packages can expect different units for different component types. Finite numbers and resolved endpoint references do not validate power flow or cascades. These records have a separate research namespace and are not merged into the physical map as certified additional assets.

Official NESO technical reports/appendices and the E3C report for **9 August 2019** are downloaded for disturbance-sequence, generation-loss, frequency/protection and load-disconnection validation. The official E3C Storm Arwen final review and ENW 2024–2025 performance report provide regional storm-impact/restoration checks. Neither kind of report supplies a complete, matched pre-event bus/relay model.

Raw public evidence is indexed in `data/grid/raw/research/regional_evidence_manifest.json`; the final Arwen review is also added by the collector. SPEN's direct storm/performance PDF downloads returned HTTP 403, which is recorded rather than represented as a successful download. Open metadata and available web publication references are documented separately.

Observed weather adds **18 station-years at nine stations** for 2021 and 2025: Belfast Aldergrove, Belfast City, Prestwick, Edinburgh, Eskdalemuir, Shap, Newcastle, Leeming and Leeds Bradford. These NOAA ISD/Global Hourly observations can check reanalysis wind estimates during Arwen and Éowyn. Raw coded measurements, missing-value conventions and quality flags are retained; a station is not an instrument on the asset. ISD stops on 24 August 2025, so 2025 files are partial-year even where complete for the January storm. [NOAA's service migration notice](https://www.nesdis.noaa.gov/news/service-location-change-integrated-surface-data-global-hourly) directs current data users to GHCNh; this collection does not claim observed weather for the 2026 live incidents. The downloader uses NOAA's replacement public S3 delivery location, validates the CSV schema and rejects HTML listings.

## What is still needed for the requested models?

The machine-readable assessment is [`model_data_readiness.json`](../data/grid/reports/model_data_readiness.json). It distinguishes acquired, partial and missing evidence.

For a **calibrated circuit/asset failure model**, the highest-value missing data are:

1. Full SPEN HV/LV historical fault records for SPD/southern Scotland, with explicit unplanned classification, actual onset/restoration, circuit codes, equipment IDs and a certified GIS/CIM crosswalk.
2. NPG's crosswalk for the recovered `networkObject` IDs, plus full historical fault/state records and the asset roster valid at each event date. ENWL's full historical unplanned fault export would expand northern England beyond the supplied subset.
3. NIE's historical NaFIRS/DMS incident and interruption-stage logs, with primary/feeder/transformer IDs, actual damage/repair details, timestamps and affected supply areas. Current Powercheck snapshots cannot reconstruct those histories.
4. Asset in-service/retirement and reporting/state histories, to identify actual at-risk exposure and valid negative observations. Unknown periods remain unknown, irrespective of whether we analyse collection gaps.
5. Age, materials, condition/inspection, maintenance, vegetation clearance, overhead/underground construction, hazard observations and their historical validity. Assumed pylon height alone is insufficient vulnerability information.

For a **validated nationwide cascade simulation**, additionally obtain:

- Matched bus-breaker/terminal topology, switch states, line/transformer parameters, ratings, taps and operating limits.
- Time-aligned bus active/reactive demand, generator dispatch and capability, interconnector flows and actual availability for each validation event.
- Relay settings/delays, breaker timings, reclose logic, UFLS/RoCoF protection, generator governors/exciters/inertia and inverter control models appropriate to the cascade type.
- High-resolution event/fault/relay logs and voltage/frequency/power traces to compare with the simulated sequence.

**GB and Northern Ireland require different synchronous-system models.** NI is part of the all-island synchronous system; model the Republic of Ireland or a defensible equivalent, and couple to GB through HVDC. Excluding ROI from the user-facing UK map does not justify deleting its electrical influence from an NI cascade model.

The raw electrical CIM/LTDS/ETYS inventories must also stay separate from the map's coordinate-only catalogue. Unlocated electrical buses can be necessary for connectivity. A solver model should use source topology and verified crosswalks, rather than treating every retained map point or shared pylon as an electrical bus.

Official SPEN historical fault records and NPG circuit/operating-data records currently return **HTTP 403** without account access. NIE's portal has registration requirements. The public feeds and source-reference downloads above do not remove those access limits. [`REGIONAL_DATA_REQUESTS.md`](REGIONAL_DATA_REQUESTS.md) provides concrete request drafts and deliverable schemas; no messages have been sent.

## Reproduce

```bash
.venv/bin/python scripts/download_uk_outages.py             # includes the public NIE feed
.venv/bin/python scripts/download_regional_evidence.py
.venv/bin/python scripts/download_regional_weather_observations.py
.venv/bin/python scripts/import_outage_archive.py
.venv/bin/python scripts/build_regional_evidence.py
.venv/bin/python scripts/finalize_regional_collection.py
npm run build --prefix frontend
.venv/bin/python -m pytest -q
.venv/bin/python scripts/validate_grid_atlas.py
```

The pinned PyPSA bundle and NPG native snapshot manifests are dependencies saved under `raw/research/pypsa_gb` and `raw/research/outage_watch`. Their exact commits and raw download URLs are recorded. The source CSV is already in its canonical raw location. After rebuilding the earlier SSEN/live register, rerun these import/build/finalise commands to restore supplemental records and references. Stop the app during database preparation.
