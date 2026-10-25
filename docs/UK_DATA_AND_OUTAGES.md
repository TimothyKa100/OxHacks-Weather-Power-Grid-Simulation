> Latest regional import and model-readiness findings: [REGIONAL_MODEL_DATA.md](REGIONAL_MODEL_DATA.md). Its current totals supersede the earlier collection counts below.

# UK map and outage evidence

This is the current guide for the land-filtered atlas and expanded outage collection. Older SSEN research outputs under `data/processed/` and source-specific intermediate files under `data/grid/normalized/` remain source archives, not the active map catalogue. The canonical map is `data/grid/uk_power_grid.sqlite`; current outage exports are `all_outage_reports.parquet` and the separate `generation_unavailability.parquet`.

## Geography, visibility and connections

The active catalogue removes records without finite longitude/latitude and points outside the **ONS Countries December 2024 BGC** land polygons. This includes Northern Ireland and UK islands, excludes the Republic of Ireland and Crown Dependencies, and removes offshore generation under the requested land-only scope. BGC follows mean high water, generalised to 20 m; coastal classification has that positional limitation. Coastal line geometry is clipped to land. Original downloads and exclusion audits remain available to explain removals and reproduce the processing.

Display thresholds are in `backend/asset_rules.py`: plants and substations at 132 kV or above from zoom 4; medium substations and rare equipment progressively from 6; local substations from 8; small generators and transformers from 10; local routes from 11; towers from 13; poles and electrical junctions from 15. Lower thresholds persist at higher zooms. Summaries count only the enabled types visible at that zoom, and may cluster dense locations. Searching can inspect any retained node independently of visibility.

Solid lines show downloaded route geometry. Dashed lines show source electrical edge relationships as straight schematics. Both endpoints must exist in the retained catalogue. There are no invented nearest-neighbour electrical edges. Mechanical support and geographic site membership do not create conducting connections. Pylons can carry electrically distinct circuits. Source inventories overlap: viewing several sources does not establish extra independent circuits. Route/edge responses are paginated and the map retrieves every page for its current viewport, cancelling requests when the view changes.

For pylons, published numeric height tags take precedence. Otherwise: explicit T-pylon designs use 35 m; L4 designs or 132 kV towers use 26 m; L2/L6 designs or towers at 275/400 kV use a 46 m midpoint with the **42–50 m** range retained. Missing tower voltage can use the highest voltage of a source-linked supported route. Unknown voltage/design stays unknown. Ground elevation remains unchanged; `asset_top_elevation_m = terrain_elevation_m + structure_height_m` only when both exist. This is structure top elevation, not surveyed conductor height, sag, insulation clearance, flood protection height or a wind-speed measurement at that elevation.

## Datasets and what each contributes

| Dataset | Use | Main limits / licence |
|---|---|---|
| [OpenStreetMap via Geofabrik](https://download.geofabrik.de/europe.html), GB and NI extracts | Mapped plants, substations, routes, towers, poles, equipment and shared-vertex topology | Incomplete mapping and tag accuracy; mapped incidence is not operator certification. © OpenStreetMap contributors, ODbL. NI clipped to its boundary. |
| [Jacob Wright's UK Grid Atlas](https://github.com/jacobwright32/uk-grid-atlas) | GB station and transmission bundles, NI records from its Ireland bundles, BMU-to-station crosswalk | Pinned commit and SHA-256 files in `raw/uk_grid_atlas/manifest.json`. Existing exact OSM IDs are enriched; other records retain `UK_GRID_ATLAS` provenance. Transmission bundles have simplified geometry and do not supply certified terminal connectivity. Foreign/offshore features are excluded. BMU station mapping includes fuzzy/manual matches. Software MIT; OSM data ODbL. |
| [NGED Connected Data](https://connecteddata.nationalgrid.co.uk/) | Four-region LTDS CIM equipment, electrical terminals, circuit and bus data, ratings, substation/location mapping, capacity context | Current published network model; historical configuration and energised state unknown. Source-specific NGED/WPD licence retained in manifests. |
| [SP Energy Networks LTDS](https://www.spenergynetworks.co.uk/pages/long_term_development_statement.aspx) | SPD/SPM existing-network electrical models and ratings | Separate source identities; no automatic outage-to-CIM identity. Source licences retained. |
| [SSEN Distribution](https://data.ssen.co.uk/) | Substations, mapped overhead routes, feeder/supply hierarchy, GIS circuits, generation and SEPD/SHEPD CIM | Hierarchy, GIS circuits and electrical models need a confirmed identity crosswalk. Mostly CC BY 4.0; exact resource licences in source manifests. |
| [Northern Powergrid](https://northernpowergrid.opendatasoft.com/) | Substation and embedded generation inventory; live incident/postcode evidence | Inventory does not supply a complete connected route model. Northern Powergrid Open Data Licence as recorded by publisher. |
| [UK Power Networks](https://ukpowernetworks.opendatasoft.com/) | Grid/primary-site source information and live faults | Some electrical/EHV record exports need registration. Live locations are aggregated postcode locations; small incidents may be omitted. CC BY 4.0 where specified. |
| [NESO ETYS 2025 Appendix B](https://www.neso.energy/publications/electricity-ten-year-statement-etys/etys-documents-and-appendices) | Transmission circuits, impedances, transformers, published site relationships | Planning snapshot, not current switch states or complete operational power flow. NESO terms retained. |
| [DESNZ DUKES](https://www.gov.uk/government/collections/digest-of-uk-energy-statistics-dukes) and [REPD](https://www.gov.uk/government/publications/renewable-energy-planning-database-monthly-extract) | Power-station and operational renewable-generation inventories, capacity/technology | Overlaps OSM and operator records; does not establish electrical terminal links. Crown copyright, OGL v3.0. |
| [OS Terrain 50](https://www.ordnancesurvey.co.uk/products/os-terrain-50) | Ground heights and route terrain profiles for GB | 50 m terrain grid; no NI terrain in this product; height above ground is a separate estimate. OGL v3.0. |
| [ONS Open Geography](https://geoportal.statistics.gov.uk/datasets/0139831c7166457ab1f7b3ab1f919cba_0/about) | Coastline-clipped UK boundary for scope filtering | 20 m generalisation, mean high water. OGL v3.0; source metadata/hash in `raw/uk_boundary_metadata.json`. |
| [ERA5 via Open-Meteo](https://open-meteo.com/en/docs/historical-weather-api) | Hourly weather at downloaded grid cells near node locations | Approximately 0.25° reanalysis, 35 km association cutoff, incomplete national sampling. Historical estimate rather than asset observation or archived forecast. Attribution retained in weather manifests. |
| [Environment Agency Recorded Flood Outlines](https://environment.data.gov.uk/dataset/8c75e700-d465-11e4-8b5b-f0def148f590) | Node-point overlap with dated flood footprints | England only, uncertain source date precision, no verified flood depth or damage. Missing overlap means unknown. OGL v3.0 with EA attribution. |
| [Forest Research National Forest Inventory](https://www.forestresearch.gov.uk/tools-and-resources/fthr/open-data/) | Existing regional SSEN route woodland proximity features | Woodland land categories, not actual tree height or historical canopy/clearance; regional coverage. Publisher attribution in woodland manifests. |
| Published postcode units via [Postcodes.io](https://postcodes.io/about/) | Centres of affected postcode units, for locating reports and candidate circuit portions | A postcode centre is not its geographic boundary or electrical supply area. Terminated/unmatched postcodes remain unresolved. OS/Royal Mail/National Statistics/NRS attribution applies; see saved postcode metadata. |

The old IEEE benchmark/pandapower demonstration is synthetic electrical simulation context, separate from real UK outage evidence. Demand/register/woodland research inputs and their exact schemas remain documented in `DATA_GUIDE.md`; they do not become physical failure labels.

## How strong was the original outage link?

The original SSEN download has **76,574 reports** spanning April 2015–October 2026. Before land filtering:

- **65,217 (85.2%)** match a candidate feeder representative point through a normalised primary/feeder NRN.
- **4,169 (5.4%)** use a candidate GIS-circuit representative point.
- **7,188 (9.4%)** have no usable mapped location.
- Thus **69,386 (90.6%)** have a candidate network location. This is association coverage, not 90.6% verified circuit identity.
- Equipment/component fields exist throughout; **45,972** report `damage=1`. These describe reported equipment categories and damage, not a unique failed pole, span, transformer or circuit-breaker asset ID.
- **Zero** reports currently establish a verified failed physical asset in the catalogue. Planning status, timezone, historical topology, actual onset/restoration and the NaFIRS-to-CIM crosswalk remain incomplete.

The current map excludes the unlocated/offshore subset rather than assigning guesses. Source and exclusion archives preserve the distinction; total source reports and active map reports are different denominators.

## New outage collection

`data/grid/raw/outages/latest_manifest.json` records URLs, retrieval times, hashes, licences, row counts and access limitations. Live downloads are snapshots collected now, **not reconstructed national history**.

| Source | Evidence acquired / limitation |
|---|---|
| UKPN `ukpn-live-faults` | Incident reference, planned/unplanned status, timestamps, customer counts, cause narrative and full affected-postcode list. A published point or postcode-derived location is an affected area, not failed equipment. |
| Northern Powergrid `live-power-cuts-data` | Incident reference, network level, reasons, updates and affected postcode/point rows. Repeated postcode rows are grouped per reference; counts are not summed. |
| NGED `live-power-cuts` | Both summary and detailed CSV feeds, merged by fault ID. Planned status, network-level category, region, coordinates, affected postcodes, available actual restoration and separate estimated restoration. |
| SSEN Powertrack live API | Incident/reference, explicit UTC times, affected postcodes, coordinates and **primary/HV feeder NRNs**. A unique retained feeder match is stronger than proximity; it is still not the failed component. |
| SPEN historical HV/LV faults | Metadata/schema and NaFIRS field definitions downloaded. The catalogues advertise 108,275 HV and 257,459 LV rows, including circuit codes and LV transformer IDs, but anonymous exports returned `[]`. These are **not downloaded outage histories**. |
| SP ENW historical unplanned outages | Metadata/schema downloaded; 313,089 advertised rows. Anonymous export returned `[]`. Historical documentation describes faults since 1984 (HV+) / 2000 (LV); current catalogue coverage says since 2000. Actual record coverage must be confirmed after authorised access. |
| UKPN EHV outage planning records | Metadata downloaded; 20,093 advertised records but anonymous export returned `[]`. Even with access, distinguish scheduled, cancelled and actual outages; do not turn maintenance plans into fault labels. |
| NIE live network faults, SPEN live/NEO, SP ENW NEO | Metadata downloaded; anonymous exports empty despite advertised records. No nationwide or NI outage history is claimed. Current account access can unlock these documented sources. |
| [Elexon REMIT](https://developer.data.elexon.co.uk/) | Public historical **unit-identified** generation/interconnector unavailability, events overlapping 2015 through collection date. 216,006 downloaded report revisions, raw IDs/MRIDs, UTC event times, cause, planned/unplanned, status, capacity and unavailable MW. Latest revision per MRID exported separately. Related MRIDs can still describe the same event. Partial derating, maintenance, cancellations and forecasts must remain distinct from customer interruptions and physical breakage. |

The SSEN historic register and its source-specific original reports remain the strongest downloaded distribution history. The live feeds broaden present geographic coverage; they cannot retrospectively fill the other operators' history. Repeated collection must preserve observation gaps and explicit source status. A disappeared incident is not proof of restoration; an ETR is never an actual restoration timestamp.

## Can postcode outages be linked to circuit regions?

Yes, as **uncertain regional observations**. The implemented `outage_circuit_candidates` table:

1. Preserves source incident IDs and lists of affected postcode units.
2. Uses an exact published SSEN primary/feeder NRN where it resolves uniquely.
3. For SSEN postcode units, queries downloaded **operator-specific** GIS route geometry within 1 km of the postcode centre and retains up to five nearby candidate sections per postcode. It stores distance, within-route fraction and the projected location on the route. These identify candidate route portions rather than an invented fault point. The same adapter supports NGED when located operator route geometry is available; the current retained NGED CIM equipment does not supply that route geometry.
4. Leaves UKPN/NPG/NGED circuit identity unresolved where no matching operator route inventory exists. It does not borrow the nearest unrelated operator's line or nearest substation.
5. Marks every spatial result `low_spatial_candidate_only`. Rank is geographic distance, **not calibrated probability**. No candidate sets `failed_asset_id`, confirms a supplying circuit or becomes a physical-failure label.

The 1 km radius is a transparent search parameter, not an accuracy guarantee. Rural postcodes can span much larger areas; parallel feeders, underground cables, interties and reconfiguration can invalidate nearest-route assumptions. An incident can affect a downstream area even though the failed upstream asset lies outside that area.

For stronger links, obtain operator postcode/UPRN-to-secondary-substation-to-feeder lookup tables, dated supply polygons and operating topology. Intersect affected supply areas, trace candidate upstream paths, enforce operator/voltage compatibility and temporal validity, and calibrate link confidence against incidents with known circuit IDs. Where an affected region spans several circuits, retain the candidate set. A postcode sector gives substantially less precision than a full postcode unit.

For modelling, keep separate targets: **component damage**, **circuit interruption**, **customer-region interruption**, and **generation unavailability**. Use postcode events as weak/set-valued supervision or for consequence validation, with operator/time/source coverage features and sensitivity analysis. Do not train every nearby asset as a positive, duplicate one event across its postcode list, infer failure-free hours from missing reports, or mix new region labels into an existing asset-failure target as if they had equal certainty. Evaluate on held-out storms and geographic regions, with separate precision/calibration for each evidence tier. Post-event cause/equipment/customer fields are labels or analysis evidence, not pre-failure predictors.

## Collection result on 25 October 2026

- 4,121,335 active UK land nodes after removing 110,879 unlocated and 6,272 outside-land records; 1,429 coastal routes clipped.
- Atlas adds 6,459 source-scoped records and enriches 3,201 existing OSM records; 1,524 foreign/offshore bundle features excluded.
- 70,191 active customer-incident reports: 69,237 retained SSEN historical reports plus 954 live reports (UKPN 526, NGED 218, SSEN 166, NPG 44). Counts are source reports; cross-source event identity is not certified.
- 164 live SSEN feeder-reference matches and 9,843 postcode/route candidate rows, covering all 166 live SSEN reports. One incident can have many candidate rows.
- 216,006 downloaded Elexon message states; 166,394 map through the Atlas station crosswalk to UK land, covering 159 unit IDs. The mapped states include 68,406 dismissed, 52,228 inactive and 45,760 active messages; these are not 166,394 independent physical failures. Unmapped messages remain outside the active geographic catalogue.
- 70,218 towers have source or assumed structure heights; 18,414 have unknown design/voltage and stay unknown.

## What would unlock the most useful additional history?

Authorised exports from SPEN HV/LV historical faults and SP ENW historical unplanned outages are the highest-priority identified gaps: their catalogues advertise **678,823 rows combined**, although duplicates, geography, reporting changes and actual failure types must be assessed after download. No registration is performed and no access control is bypassed here.

Request: incident reference plus reporting year/operator/district; explicit planned flag; actual onset, interruption stages and actual restoration in UTC; affected circuit/feeder IDs and stable equipment IDs; repaired component and damage/failure mode; postcode/secondary supply lookup and its valid dates; the circuit/asset-to-GIS/CIM identity crosswalk; and coverage/omission rules. Circuit descriptions or NaFIRS categories alone are insufficient for physical-asset identity. A provided authorised export can be ingested without changing the uncertainty policy.

## Reproduction and outputs

```bash
# Starting from the existing source-cleaned, conditions-prepared catalogue:
.venv/bin/python scripts/download_uk_outages.py
.venv/bin/python scripts/download_uk_outages.py --geocode-only
.venv/bin/python scripts/download_remit.py
.venv/bin/python scripts/refine_uk_grid.py
.venv/bin/python scripts/build_uk_outages.py
.venv/bin/python scripts/build_remit.py
.venv/bin/python scripts/refresh_uk_exports.py
npm run build --prefix frontend
.venv/bin/python -m pytest -q
```

The pinned Atlas bundles and ONS boundary must be present under `data/grid/raw/`; their exact URLs/hashes are in their saved manifests. `refine_uk_grid.py` writes a transaction against the working catalogue, so the app should be stopped during preparation. Rebuild the original cleaned catalogue first to undo geographic removals. `prepare_grid_conditions.py` rebuilds the old SSEN-only register; rerun refinement and the two new builders afterwards to restore the expanded active register. Do not use older pre-filter source exports as the new canonical map.

Generated reports: `reports/uk_refinement.json`, `reports/uk_outages.json`, `reports/generation_unavailability.json`. Exclusions: `normalized/excluded_geographic_nodes.parquet`, `excluded_geographic_outages.parquet`, `excluded_live_outages.parquet`, `excluded_unlocated_generation_reports.parquet`. The active outage exports include only retained UK land locations. The unlocated REMIT source archive is evidence, not an active geographic layer.

New API: `/api/grid/network?kind=routes|edges&bbox=...&zoom=...&hierarchical=1&after=...`; `/api/grid/outage?id=...` includes candidate circuit regions; `/api/grid/generation-outages?unit_id=...` or `?asset_id=...` paginates separate unit reports. Existing node overview/list endpoints accept `hierarchical=1` and `zoom`; without that flag they remain complete counted data views for compatibility.
