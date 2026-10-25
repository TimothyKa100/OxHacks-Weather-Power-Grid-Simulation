# Portable electrical-property enrichment

`scripts/enrich_network_electrical.py` is a standalone Python 3.10+ script with
**no third-party dependencies or repository imports**. Copy that file into another
branch/project and pass the graph path. It writes a new output and never edits or
overwrites the source graph.

It propagates **nominal voltage**, calculates electrical quantities from stated
equipment parameters, and records the method for every supplied/derived value.
Power-plant output does not determine a neighbour's local demand or line rating.
Unresolved single voltages use the neighbour-average fallback described below;
other unknown quantities stay `null` and appear in `missing_fields`. Contradictory
data appear in `issues`. An explicit assumptions file can supply a demand
allocation, operating power factor, dispatch fraction or known conductor types.

## Run it

JSON graph, with no added equipment/demand assumptions:

```bash
python3 scripts/enrich_network_electrical.py graph.json --output graph.enriched.json
```

**Neighbour averaging is enabled by default.** After ordinary voltage propagation,
each unresolved single voltage takes the arithmetic mean of its known, distinct
connected neighbours. All averages in one round use the preceding snapshot;
new estimates can supply neighbours in the next round. Each missing value is
filled once, so this is frontier-based imputation, not a converged power-flow or
harmonic interpolation solution. Known voltages remain unchanged.

Estimates have `voltage_is_estimate: true` and provenance method
`neighbour_average_estimate`, with the round and neighbour count recorded.
For example, known neighbours at 132 and 400 kV yield an estimate of 266 kV;
that value is an imputation and need not be a real nominal voltage class.
Conflicting-source flags remain present. Voltage-dependent per-unit impedance
and current-based ratings are recalculated and tagged as using estimated voltage.

This fallback uses electrical/support connections within a source layer. It
respects transformer boundaries and open/unknown switches. Multi-voltage
transformers and pylons keep their voltage lists instead of receiving a scalar
average. Isolated components with no eligible known voltage remain unresolved.
For ordinary node-link JSON, each distinct neighbouring node counts once,
regardless of parallel edges; normalized branch voltage is a secondary fallback
when no original neighbouring node has a known voltage.

To disable estimation and retain the original propagation-only behaviour:

```bash
python3 scripts/enrich_network_electrical.py graph.json \
  --output graph.enriched.json --voltage-fallback none
```

The existing national equipment graph, producing a queryable SQLite sidecar:

```bash
python3 scripts/enrich_network_electrical.py data/grid/uk_power_grid.sqlite \
  --source NGED:SOUTH_WALES --output data/grid/south_wales_electrical.sqlite
```

Omit `--source` to process every source layer. Different layers remain separate.
The large SQLite adapter streams records, skips geometry blobs and stores its
working data on disk. It uses a compact union-find array (approximately five
bytes per entity) for voltage connectivity. Reserve disk space for the output
and staging file; full-national processing has not been benchmarked. The source
database is opened read-only. Start with one source layer to inspect the results.

Runnable small example, including **illustrative** conductor constants and a
50 MW total load allocated 20/30 MW to two substations:

```bash
python3 scripts/enrich_network_electrical.py scripts/examples/electrical_network.json \
  --assumptions scripts/examples/electrical_assumptions.json \
  --output /tmp/electrical-example.json --strict
```

Both pylons receive 132 kV, the low-voltage substation receives 33 kV from an
explicit transformer terminal mapping, and local pylon demand/generation are
zero. Branch impedance and rating come from conductor/nameplate inputs. The
example constants are not a lookup table suitable for every 132 kV circuit.

`--strict` writes the result/report and then exits with code 2 if required fields
or consistency checks remain unresolved. Normal completion is code 0; malformed
input fails with code 1. Existing output paths are rejected. Choose a new output
name for each run. The summary report is printed as JSON to stdout; progress goes
to stderr.

## Input layouts

**Ordinary node-link JSON:** `nodes` plus `edges` or NetworkX-style `links`.
Nodes use `id`, `type`/`kind`/`node_type`; edges use `source`/`target`, `from`/`to`,
or `from_node`/`to_node`. IDs can be arbitrary strings or integers, but must be
unique after string conversion. A branch without an ID gets `edge:<array index>`.
The repository's `buses`/`branches`/`generators` format is also recognized.

A supplied root `generators` array declares the complete generator list for a
bus/branch model; bus/junction nodes absent from it receive zero generation unless
they already have explicit values. Omitting that array leaves unreported
generation unresolved. Lines default to in service; an explicitly unknown state
blocks propagation. These defaults are recorded in provenance.

In this layout, ordinary edges declare actual electrical connectivity. A pylon
on a simple single-circuit chain may act as a junction in that declared model.
If one pylon carries distinct circuits, use circuit-specific port/junction nodes
and mechanical `supports` relationships. Explicit different `circuit_id` values
on branches sharing a pylon also make that pylon a voltage-propagation boundary.
The script cannot recover missing circuit separation from an unlabeled graph.

**Equipment-incidence JSON:** set `"representation": "incidence"`, or pass
`--layout incidence`. Lines/transformers are nodes and edges describe their
terminal connections. Mechanical support nodes are always propagation boundaries.

**Equipment-incidence SQLite:** default tables are `nodes`, `edges` and optional
`relationships`. Node identity/type are `node_id`/`node_type`; link endpoints are
`from_node`/`to_node`. This directly supports `data/grid/uk_power_grid.sqlite`.
`candidate_edges` are not used. The optional source filter is an exact match.

The accepted electrical relations are `electrical_connection`,
`electrical_terminal`, `line_terminal` and `transformer_terminal`. Missing
relations on ordinary edges default to declared electrical connectivity.
Containment, geographical proximity, supply hierarchies and generic membership
do not establish electrical connections.

Attributes can be top-level or in `properties`, `attributes` or `properties_json`.
Known CIM numeric fields are recognized, including line R/X, generation capacity,
load P and transformer windings. OSM voltage tags are converted from volts to kV
only when their OSM origin/power tagging is explicit. Multi-voltage tags such as
`400000;132000` stay multi-voltage; the existing atlas's scalar maximum does not
erase the other voltage class.

## Adapt field names in another branch

Supply `--mapping mapping.json`, for example:

```json
{
  "nodes_key": "vertices",
  "edges_key": "connections",
  "node_fields": {
    "id": "assetId",
    "kind": "assetType",
    "voltage_kv": "nominalVoltageKV",
    "load_mw": "demandMW"
  },
  "edge_fields": {
    "id": "connectionId",
    "from": "startId",
    "to": "endId",
    "length_km": "routeLengthKM"
  }
}
```

SQLite table names can be mapped with `nodes_table`, `edges_table` and
`relationships_table`. Attribute mappings use the canonical field names listed
in the script's `FIELDS` constant, plus `id`, `kind`, `layer`, `in_service`,
`closed`, `from` and `to`. Mappings name exact fields in the flattened record;
they do not infer units. Convert differently scaled input units explicitly.

Custom node types can be mapped through the assumptions file, for example
`"type_aliases": {"mast": "pylon", "station_bus": "bus"}`.

## What propagates and what is calculated

| Property | Rule |
| --- | --- |
| Nominal voltage | Collect all voltage anchors in a same-voltage connected component. Fill missing voltage only if anchors agree. Source voltages can originate at plants, substations, buses or conductors. |
| Shared-pylon voltage | Read connected/supported circuit voltage levels. A support carrying 132 and 400 kV gets `voltage_levels_kv: [132,400]` and scalar `voltage_kv: null`. It never becomes a bridge between those circuits. |
| Transformer sides | Keep winding regions separate. Seed an endpoint only from an explicit winding-to-node mapping. Never infer which terminal is HV from traversal order or geographic position. |
| Switches | Traverse only explicitly closed switches. Unknown/open switches are boundaries. `Switch.normalOpen` may provide a labeled normal-state assumption, not a live state. |
| Local demand | Preserve supplied MW. Passive equipment gets zero local demand. Substation/load-bus demand requires input or explicit total/weight allocation. |
| Generation | Preserve provided capacity/output, or sum explicitly attached generator records. Never copy plant capacity onto downstream pylons or substations. |
| Line R/X | Preserve total ohms, or calculate from per-km impedance, route length and explicitly identical parallel circuits. |
| Per-unit R/X | Convert using the stated MVA and voltage bases. Input per-unit values need an input `base_mva` or equipment `impedance_base_mva`. |
| Line MVA limit | Calculate from line-to-line kV, current in kA, parallel circuits and derating factor. Voltage alone cannot establish ampacity. |
| MW limit | Preserve supplied MW or calculate MVA × an explicitly provided power factor. No implicit unity power factor. |
| Transformer impedance | Convert two-winding R/X on their individual voltage bases, or use rated MVA and both short-circuit impedance and resistance percentages. Multi-winding devices need a separate model. |
| Tap | Ordinary lines get 1. Transformer operating tap remains unknown unless supplied or `nominal_taps: true` is explicit. Physical turns ratio is stored separately. |

Conflicting voltage anchors quarantine physical missing-voltage inference across
the entire component. The default averaging fallback may then assign explicitly
labeled estimates to unresolved single voltages. With `--voltage-fallback none`,
those values stay unresolved. Known source values and conflict flags remain
visible. Results are independent of record order. Cross-source electrical links
are blocked because the source atlas contains overlapping representations.

These are nominal quantities. Voltage drop, actual current, power flow and
overload state require a separate power-flow calculation using a complete
operating point. This script does not infer those values from graph distance.

## Units and formulas

For identical parallel line circuits, when per-km parameters describe one circuit:

```text
R_equivalent_ohm = r_ohm_per_km × length_km / parallel
X_equivalent_ohm = x_ohm_per_km × length_km / parallel
Z_base_ohm       = voltage_kv² / base_mva
x_pu            = X_equivalent_ohm / Z_base_ohm
S_limit_MVA     = √3 × voltage_kv × max_i_ka × parallel × derating_factor
P_limit_MW      = S_limit_MVA × power_factor
```

Existing total impedances and per-unit values already describe the supplied
equivalent branch and are not divided by `parallel` again. `parallel` means
electrically parallel identical circuits, not a count of conductors on a pylon.
Resistance and current limits need conductor data; there is no automatic
voltage-to-conductor guess. Supplied `rating_mva`/`capacity_mva` is an aggregate
equipment limit. `sn_mva`/`unit_rating_mva` is the rating of one transformer unit.

For a two-winding transformer, per-unit impedance on the system MVA base and
its corresponding rated winding voltage bases is:

```text
z_nameplate_pu = vk_percent / 100
r_nameplate_pu = vkr_percent / 100
x_nameplate_pu = √(z_nameplate_pu² − r_nameplate_pu²)
x_system_pu   = x_nameplate_pu × base_mva / total_parallel_rating_mva
```

For explicit winding impedances, sum `X_winding × base_mva / V_winding²` across
the two windings (and divide by the number of identical parallel transformers).
The equivalent ohms are reported with an explicit winding voltage reference.
Scalar transformer ohms supplied without `impedance_voltage_kv` cannot be
assigned to a winding side. Zero series impedance is reported as requiring
topology contraction before the DC solve; no arbitrary epsilon is inserted.

Transformer per-unit calculations use the stated rated winding voltage bases.
An explicit winding impedance reference must match one of those ratings. General
off-rated voltage-base/tap-dependent transformer conversion belongs in the
power-flow adapter rather than being inferred from an ambiguous side reference.

The formulas and parameter conventions follow the primary documentation for
[pandapower lines](https://pandapower.readthedocs.io/en/stable/elements/line.html)
and [two-winding transformers](https://pandapower.readthedocs.io/en/stable/elements/trafo.html).

For transformer edges, use `hv_bus`/`lv_bus` together with `hv_kv`/`lv_kv`, or
explicit `from_voltage_kv`/`to_voltage_kv`. For incidence nodes, provide:

```json
{
  "id": "transformer-1",
  "type": "transformer",
  "terminals": [
    {"node_id": "hv-bus", "voltage_kv": 132},
    {"node_id": "lv-bus", "voltage_kv": 33}
  ]
}
```

Each terminal mapping must also have an electrical edge to that transformer.
Two `windings` records with `node_id`, `voltage_kv`, `r_ohm` and `x_ohm` combine
terminal mapping and winding impedance. Existing CIM winding field names are
also accepted. The atlas's old terminal-incidence table sometimes omits the CIM
terminal ID; the script will not reconstruct missing winding identity by guessing.

## Explicit assumptions

```json
{
  "power_factor": 1.0,
  "dispatch_fraction": 0.5,
  "nominal_taps": true,
  "total_load_mw": 30000,
  "demand_weights": {"substation-A": 3, "substation-B": 1},
  "line_types": {
    "known-conductor": {"r_ohm_per_km": 0.1, "x_ohm_per_km": 0.3, "max_i_ka": 0.8}
  }
}
```

Only include assumptions you intend to make. An entity selects a conductor
template with `"line_type": "known-conductor"`; explicit entity values take
precedence. The defaults do not contain a guessed conductor library.

`total_load_mw` covers the selected graph. All supplied loads are preserved and
subtracted first. The remaining demand is assigned to missing-load nodes in
`demand_weights`, proportional to their positive weights. The final allocation
conserves the total (with a final residual adjustment). Unknown node IDs,
passive-equipment demand recipients and totals below existing loads are rejected.

`dispatch_fraction` fills missing output from capacity, with an explicit
assumption label. It does not balance network demand or model renewable
availability. A subsequent power-flow dispatch must handle that operating point.

## Outputs and integration

JSON preserves original fields and appends an `electrical` object to each node
and physical branch. Read **`node.electrical.voltage_kv`**, not the untouched
source voltage field, for the resolved value. `electrical` contains:

- Derived/provided properties, voltage levels and impedance base.
- Per-property `provenance`, including component/anchor references for voltage.
- `issues`, `missing_fields`, `voltage_conflict` and `electrical_fields_complete`.
- `voltage_is_estimate: true` when a neighbour average supplied the voltage.

The root `electrical_enrichment` contains counts, unresolved-field totals and
all explicit assumptions. This operation does not certify a graph as a complete
cascade network, so `cascade_ready` remains false even if all checked fields
are populated. Connectivity identity, transformer port mapping and an operating
point still need validation before running the solver.

SQLite output has `node_electrical` and `edge_electrical` views, plus normalized
entities, links, voltage components and metadata. The original source IDs are
retained for joins. Example:

```sql
SELECT node_id, voltage_kv, voltage_levels_kv, load_mw, x_pu, rating_mw
FROM node_electrical;

SELECT node_id, json_extract(properties_json, '$.missing_fields')
FROM node_electrical
WHERE json_array_length(json_extract(properties_json, '$.missing_fields')) > 0;
```

This is a property-enrichment script, not an equipment-incidence-to-bus network
converter. Keep line impedance/rating on the conductor record. The existing
simulator additionally expects its own bus numbering, branch endpoints, network
coordinates and weather/fragility fields; those are separate adapters.

## Verification

```bash
.venv/bin/python -m pytest tests/test_network_enrichment.py -q
```

Tests cover radial/loop propagation, conflicting anchors, transformer-side
mapping, winding-reference arithmetic, source separation, shared supports,
switch states, parallel circuits, unit conversion, demand conservation,
generator attachment, custom field mappings, read-only SQLite input and CLI
overwrite protection. Averaging checks include synchronous rounds, distinct
neighbours, order independence, recalculated electrical quantities, preserved
multi-voltage equipment and disconnected components without known voltage.
