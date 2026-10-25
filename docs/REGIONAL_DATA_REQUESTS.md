# Prepared regional data requests

These are drafts for the user to submit through the operators' published data-request channels. No email, account registration or external submission has been made.

## SP Energy Networks: southern Scotland / SPD

Purpose: research calibration of weather-related circuit interruptions and validation of downstream supply loss, with a later asset-damage model. Please provide authorised exports of `all-hv-faults-spd-spm` and `all-lv-faults-spd-spm`, restricted initially to SPD if necessary. We can consume CSV/Parquet and preserve source licences and access conditions.

Required keys: licence, reporting year, master/job/split reference, stable circuit code, circuit/plant description, LV transformer ID and substation, cause/equipment/component codes, damage and physical repair/replacement, planned/unplanned status, actual onset and actual restoration stages with timezone. Please include the circuit/transformer-to-GIS/LTDS/CIM identity crosswalk and its valid dates. The public metadata advertises the relevant fields, while anonymous row access returns 403.

Also request the dated asset/circuit roster, commissioning/retirement/reconfiguration history, incident reporting exclusions and intervals over which reporting and asset operating states are complete. This is needed to avoid treating unknown observations as failure-free operation.

## Northern Powergrid: northern England

We have archived reports containing the source field `networkObject`; 1,622 supplied reports match a single native object reference by incident reference and reported time. Please clarify the object class represented and provide a certified crosswalk to network assets, primary/secondary substations, feeders and GIS/CIM IDs. Reference examples can be taken from `data/grid/normalized/npg_reported_network_objects.parquet` with incident reference, native logged time and the original source snapshot URL.

Please provide historical HV/LV NaFIRS/DMS incidents, actual interruption/restoration stages, damage/repair fields, explicit planned status, object/circuit identities, relevant location precision and timestamps. Please also provide authorised LTDS Appendix 3 circuit data and historical circuit operational measurements (132/66/33 kV); public anonymous record probes returned 403. Include in-service/state histories, reporting completeness and asset condition/age/maintenance/vegetation covariates where shareable.

## SP Electricity North West: northern England

Please provide authorised `unplanned-outages` historical records and the accompanying data dictionary, with circuit/equipment identity or the best available crosswalk. We currently have only a small archive subset and the catalogue metadata. Please specify actual versus estimated restoration, historic changes to incident reporting rules and whether records represent initial incidents, restoration stages or repeated observations.

## NIE Networks: Northern Ireland

Please provide historical unplanned NaFIRS/DMS fault and interruption-stage records, including explicit primary/substation, feeder, transformer and physical equipment identifiers; cause/component/damage and repair/replacement; actual onset and actual restoration with timezone; coordinates and their meaning; planned/unplanned classification; and incident deduplication rules. Priority validation events are Storm Éowyn in January 2025 and additional severe-weather and quiet-weather periods selected with the operator.

We have public Powercheck snapshots and the Utility Regulator's Éowyn class-level fault/CI/CML targets. Please clarify why the narrative count of more than 5,000 faults differs from the 2,687-fault population in Table 2.2 of the April 2026 resilience consultation, and provide the relevant population definitions and event-level records.

Please include dated supply-area/postcode-to-substation-to-feeder mappings, GIS/CIM identity crosswalks, in-service/retirement and switching histories, reporting rules and asset condition/vegetation-clearance covariates. A postcode sector centre cannot identify a failed circuit by itself.

## SONI / NIE transmission: all-island electrical model and validation case

Please provide the applicable machine-readable PSS/E or equivalent all-island transmission case, including a matched historical case for a selected NI validation event, under the relevant usage licence. The TYTFS provides useful topology/parameters and planning dispatch, but a matched operational case is needed for validation.

Required: bus/terminal/switch topology and state; line/transformer R/X/B and ratings; taps/shunts; bus P/Q demand; generator dispatch/capability; HVDC flows/control; generator and inverter dynamic/control models; protection/reclose/UFLS/RoCoF settings and delays; and high-resolution event, relay, frequency, voltage and power records. Clarify all per-unit bases, sign conventions, timestamps, external-network equivalents and confidentiality constraints.

The GB interface should be modelled through HVDC, while retaining an all-island NI/ROI model or a validated external equivalent. We are not requesting that a geographically filtered map be treated as the operational electrical model.
