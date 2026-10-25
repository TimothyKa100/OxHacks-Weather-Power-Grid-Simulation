"""Electrical inference must respect topology, units, boundaries and conservation."""
import copy
import json
import math
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.enrich_network_electrical import Enricher, import_json, export_json, import_sqlite, validate_assumptions, validate_mapping


def enrich(graph, assumptions=None, base=100, layout='node-link', mapping=None, voltage_fallback='none'):
    graph = copy.deepcopy(graph)
    engine = Enricher(':memory:', base, assumptions, layout, voltage_fallback)
    try:
        node_key, edge_key = import_json(engine, graph, mapping or {})
        engine.propagate()
        engine.derive()
        return export_json(engine, graph, node_key, edge_key, mapping or {})
    finally:
        engine.db.close()


def by_id(result, key='nodes'):
    return {str(n['id']): n['electrical'] for n in result[key]}


def radial():
    return {'nodes': [
        {'id': 'plant', 'type': 'generation', 'voltage_kv': 132, 'generation_capacity_mw': 100, 'generation_mw': 40, 'load_mw': 0},
        {'id': 'p1', 'type': 'pylon'}, {'id': 'p2', 'type': 'pylon'},
        {'id': 'sub', 'type': 'substation', 'load_mw': 40}],
        'edges': [{'id': 'a', 'source': 'plant', 'target': 'p1'},
                  {'id': 'b', 'source': 'p1', 'target': 'p2'},
                  {'id': 'c', 'source': 'p2', 'target': 'sub'}]}


def test_radial_voltage_propagation_does_not_copy_generation_or_load():
    graph = radial()
    result = enrich(graph)
    nodes = by_id(result)
    assert all(n['voltage_kv'] == 132 for n in nodes.values())
    assert nodes['p1']['load_mw'] == nodes['p2']['load_mw'] == 0
    assert nodes['p1']['generation_capacity_mw'] == 0
    assert nodes['sub']['load_mw'] == 40
    assert nodes['sub']['generation_capacity_mw'] is None
    assert nodes['plant']['generation_capacity_mw'] == 100
    assert 'electrical' not in graph['nodes'][0]  # input object remains untouched
    assert nodes['p1']['provenance']['voltage_kv']['method'] in ('connected_nominal_voltage', 'carried_circuit_voltages')


def test_conflicting_loop_anchors_quarantine_inference_independent_of_order():
    graph = radial()
    graph['nodes'][-1]['voltage_kv'] = 400
    graph['edges'].append({'id': 'loop', 'source': 'p1', 'target': 'sub'})
    forward = by_id(enrich(graph))
    graph['nodes'].reverse()
    graph['edges'].reverse()
    backward = by_id(enrich(graph))
    assert forward['p1']['voltage_kv'] is None
    assert forward['p2']['voltage_kv'] is None
    assert forward['plant']['voltage_kv'] == 132 and forward['sub']['voltage_kv'] == 400
    assert all(n['voltage_conflict'] for n in forward.values())
    assert {k: n['voltage_kv'] for k, n in forward.items()} == {k: n['voltage_kv'] for k, n in backward.items()}


def test_neighbour_average_fills_conflicting_region_without_overwriting_known_values():
    graph = {'nodes': [{'id': 'a', 'voltage_kv': 100}, {'id': 'b', 'voltage_kv': 200},
                       {'id': 'c', 'voltage_kv': 400}, {'id': 'missing'}],
             'edges': [{'source': node, 'target': 'missing'} for node in ['a', 'b', 'c']]}
    result = enrich(graph, voltage_fallback='neighbour-average')
    nodes = by_id(result)
    assert nodes['missing']['voltage_kv'] == pytest.approx(700/3)
    assert nodes['missing']['voltage_is_estimate']
    assert nodes['missing']['voltage_conflict']
    assert nodes['missing']['provenance']['voltage_kv']['neighbour_count'] == 3
    assert [nodes[n]['voltage_kv'] for n in ['a', 'b', 'c']] == [100, 200, 400]
    assert result['electrical_enrichment']['counts']['voltage_average_estimates'] > 0
    assert by_id(enrich(graph, voltage_fallback='none'))['missing']['voltage_kv'] is None


def test_neighbour_average_uses_distinct_nodes_not_duplicate_parallel_connections():
    graph = {'nodes': [{'id': 'a', 'voltage_kv': 100}, {'id': 'b', 'voltage_kv': 200}, {'id': 'missing'}],
             'edges': [{'source': node, 'target': 'missing'} for node in ['a', 'a', 'b']]}
    node = by_id(enrich(graph, voltage_fallback='neighbour-average'))['missing']
    assert node['voltage_kv'] == 150
    assert node['provenance']['voltage_kv']['neighbour_count'] == 2


def test_neighbour_average_rounds_are_order_independent_and_disconnected_nodes_stay_unknown():
    graph = {'nodes': [{'id': 'a', 'voltage_kv': 100}, {'id': 'b', 'voltage_kv': 300},
                       *[{'id': n, 'type': 'junction'} for n in ['u1', 'u2', 'u3', 'isolated']]],
             'edges': [{'from_node': 'u1', 'to_node': 'u2'}, {'from_node': 'u2', 'to_node': 'u3'}],
             'relationships': [{'from_node': 'a', 'to_node': 'u1', 'relation': 'supports'},
                               {'from_node': 'b', 'to_node': 'u3', 'relation': 'supports'}]}
    first = enrich(graph, layout='incidence', voltage_fallback='neighbour-average')
    assert [by_id(first)[n]['voltage_kv'] for n in ['u1', 'u2', 'u3']] == [100, 200, 300]
    assert by_id(first)['isolated']['voltage_kv'] is None
    assert by_id(first)['u2']['provenance']['voltage_kv']['round'] == 2
    graph['nodes'].reverse();graph['edges'].reverse();graph['relationships'].reverse()
    second = enrich(graph, layout='incidence', voltage_fallback='neighbour-average')
    assert {k: v['voltage_kv'] for k, v in by_id(first).items()} == {k: v['voltage_kv'] for k, v in by_id(second).items()}


def test_average_recalculates_voltage_dependent_impedance_and_rating():
    graph = {'nodes': [{'id': 'a', 'voltage_kv': 100}, {'id': 'b', 'voltage_kv': 200},
                       {'id': 'line', 'type': 'power_line', 'x_ohm': 3, 'max_i_ka': .5}],
             'edges': [{'from_node': 'line', 'to_node': node} for node in ['a', 'b']]}
    line = by_id(enrich(graph, {'power_factor': 1}, layout='incidence', voltage_fallback='neighbour-average'))['line']
    assert line['voltage_kv'] == 150
    assert line['x_pu'] == pytest.approx(3 * 100 / 150**2)
    assert line['rating_mw'] == pytest.approx(math.sqrt(3) * 150 * .5)
    assert line['provenance']['x_pu']['uses_estimated_voltage']


def test_average_preserves_multiple_windings_and_respects_switches_and_source_layers():
    graph = {'nodes': [{'id': 'a', 'source': 'A', 'voltage_kv': 132},
                       {'id': 'b', 'source': 'A'}, {'id': 'other', 'source': 'B'},
                       {'id': 'T', 'source': 'A', 'type': 'transformer', 'hv_kv': 132, 'lv_kv': 33},
                       {'id': 'pylon', 'source': 'A', 'type': 'tower', 'voltage_levels_kv': [132, 400]}],
             'edges': [{'id': 'open', 'source': 'a', 'target': 'b', 'type': 'switch', 'closed': False},
                       {'id': 'cross', 'source': 'a', 'target': 'other'},
                       {'id': 'to-t', 'source': 'a', 'target': 'T'}]}
    nodes = by_id(enrich(graph, voltage_fallback='neighbour-average'))
    assert nodes['b']['voltage_kv'] is None
    assert nodes['other']['voltage_kv'] is None
    assert nodes['T']['voltage_kv'] is None and nodes['T']['voltage_levels_kv'] == [33, 132]
    assert nodes['pylon']['voltage_kv'] is None and nodes['pylon']['voltage_levels_kv'] == [132, 400]


@pytest.mark.parametrize('state,expected', [({'closed': True}, 132), ({'closed': False}, None), ({}, None), ({'properties': {'Switch.normalOpen': 'false'}}, 132)])
def test_switch_state_controls_voltage_propagation(state, expected):
    graph = {'nodes': [{'id': 's', 'type': 'bus', 'voltage_kv': 132}, {'id': 'b', 'type': 'bus'}],
             'edges': [{'id': 'switch', 'source': 's', 'target': 'b', 'type': 'switch', **state}]}
    result = enrich(graph)
    assert by_id(result)['b']['voltage_kv'] == expected


def test_shared_mechanical_pylon_preserves_multiple_circuits_without_shorting():
    graph = {'nodes': [{'id': 'line132', 'type': 'power_line', 'voltage_kv': 132},
                       {'id': 'line400', 'type': 'power_line', 'voltage_kv': 400},
                       {'id': 'pylon', 'type': 'tower'}, {'id': 'junction', 'type': 'junction'}],
             'edges': [{'from_node': 'line132', 'to_node': 'junction'}],
             'relationships': [{'from_node': 'pylon', 'to_node': 'line132', 'relation': 'supports'},
                               {'from_node': 'pylon', 'to_node': 'line400', 'relation': 'supports'}]}
    result = enrich(graph, layout='incidence')
    nodes = by_id(result)
    assert nodes['pylon']['voltage_kv'] is None
    assert nodes['pylon']['voltage_levels_kv'] == [132, 400]
    assert nodes['junction']['voltage_kv'] == 132
    assert result['electrical_enrichment']['counts']['voltage_conflict_components'] == 0


def test_shared_support_with_explicit_distinct_circuit_ids_is_a_boundary():
    graph = {'nodes': [{'id': 'a', 'type': 'bus', 'voltage_kv': 132},
                       {'id': 'b', 'type': 'bus', 'voltage_kv': 400}, {'id': 'p', 'type': 'pylon'}],
             'edges': [{'source': 'a', 'target': 'p', 'circuit_id': 'one'},
                       {'source': 'b', 'target': 'p', 'circuit_id': 'two'}]}
    result = enrich(graph)
    assert by_id(result)['p']['voltage_levels_kv'] == [132, 400]
    assert not by_id(result)['a']['voltage_conflict']


def test_containment_candidate_and_cross_source_links_do_not_propagate():
    graph = {'nodes': [{'id': 'a', 'source': 'operatorA', 'type': 'substation', 'voltage_kv': 132},
                       {'id': 'b', 'source': 'operatorA', 'type': 'bus'},
                       {'id': 'c', 'source': 'operatorB', 'type': 'bus'}],
             'edges': [{'from_node': 'a', 'to_node': 'c'}],
             'relationships': [{'from_node': 'a', 'to_node': 'b', 'relation': 'contains'}],
             'candidate_edges': [{'from_node': 'a', 'to_node': 'b'}]}
    nodes = by_id(enrich(graph, layout='incidence'))
    assert nodes['b']['voltage_kv'] is None and nodes['c']['voltage_kv'] is None


def test_line_impedance_parallel_rating_and_per_unit_math():
    graph = {'nodes': [{'id': 'a', 'voltage_kv': 132}, {'id': 'b'}],
             'edges': [{'id': 'line', 'source': 'a', 'target': 'b', 'length_km': 10,
                        'r_ohm_per_km': .1, 'x_ohm_per_km': .3, 'parallel': 2, 'max_i_ka': .5, 'derating_factor': .8}]}
    line = by_id(enrich(graph, {'power_factor': .9}, base=100), 'edges')['line']
    assert line['r_ohm'] == .5 and line['x_ohm'] == 1.5
    assert line['x_pu'] == pytest.approx(1.5 * 100 / 132**2)
    assert line['rating_mva'] == pytest.approx(math.sqrt(3) * 132 * .5 * 2 * .8)
    assert line['rating_mw'] == pytest.approx(line['rating_mva'] * .9)
    assert line['tap'] == 1
    assert not line['issues']
    unassumed = by_id(enrich(graph), 'edges')['line']
    assert unassumed['rating_mw'] is None  # do not silently equate MVA and MW


def test_existing_per_unit_values_are_rebased_and_not_double_counted_parallel():
    graph = {'base_mva': 50, 'buses': [{'id': 1, 'kv': 132, 'load_mw': 0}, {'id': 2, 'load_mw': 5}],
             'branches': [{'id': 'L', 'from': 1, 'to': 2, 'x_pu': .1, 'parallel': 2}],
             'generators': [{'bus': 1, 'max_mw': 10, 'base_mw': 5}]}
    result = enrich(graph, base=100)
    line = by_id(result, 'branches')['L']
    assert line['x_pu'] == pytest.approx(.2)
    assert line['x_ohm'] == pytest.approx(.1 * 132**2 / 50)
    assert by_id(result, 'buses')['1']['generation_mw'] == 5
    assert by_id(result, 'buses')['2']['generation_mw'] == 0


def test_transformer_side_anchors_nameplate_math_and_tap_are_separate():
    graph = {'nodes': [{'id': 'hv', 'type': 'bus'}, {'id': 'lv', 'type': 'bus'}],
             'edges': [{'id': 'T', 'type': 'transformer', 'source': 'hv', 'target': 'lv',
                        'hv_bus': 'hv', 'lv_bus': 'lv', 'hv_kv': 132, 'lv_kv': 33,
                        'sn_mva': 40, 'vk_percent': 10, 'vkr_percent': 1, 'parallel': 2}]}
    result = enrich(graph, {'power_factor': 1, 'nominal_taps': True})
    assert by_id(result)['hv']['voltage_kv'] == 132
    assert by_id(result)['lv']['voltage_kv'] == 33
    transformer = by_id(result, 'edges')['T']
    assert transformer['voltage_kv'] is None
    assert transformer['nominal_turns_ratio'] == 4
    assert transformer['tap'] == 1  # not the physical turns ratio of four
    assert transformer['rating_mw'] == 80
    assert transformer['x_pu'] == pytest.approx(math.sqrt(.1**2 - .01**2) * 100 / 80)
    assert transformer['r_pu'] == pytest.approx(.01 * 100 / 80)
    assert not transformer['issues']
    assert transformer['x_ohm'] == pytest.approx(transformer['x_pu'] * 132**2 / 100)
    assert by_id(enrich(graph), 'edges')['T']['tap'] is None


def test_two_winding_incidence_uses_voltage_reference_for_each_winding():
    graph = {'nodes': [{'id': 'hv', 'type': 'junction'}, {'id': 'lv', 'type': 'junction'},
                       {'id': 'T', 'type': 'transformer', 'capacity_mva': 40,
                        'windings': [{'node_id': 'hv', 'voltage_kv': 132, 'r_ohm': 1, 'x_ohm': 10},
                                     {'node_id': 'lv', 'voltage_kv': 33, 'r_ohm': .1, 'x_ohm': 1}]}],
             'edges': [{'from_node': 'T', 'to_node': 'hv'}, {'from_node': 'T', 'to_node': 'lv'}]}
    nodes = by_id(enrich(graph, layout='incidence'))
    assert nodes['hv']['voltage_kv'] == 132 and nodes['lv']['voltage_kv'] == 33
    assert nodes['T']['x_pu'] == pytest.approx(10 * 100 / 132**2 + 1 * 100 / 33**2)
    assert nodes['T']['x_ohm'] == pytest.approx(10 + (132/33)**2)
    assert nodes['T']['impedance_voltage_kv'] == 132
    assert not nodes['T']['issues']


def test_transformer_does_not_guess_unknown_terminal_assignment():
    graph = {'nodes': [{'id': 'hv', 'type': 'bus', 'voltage_kv': 132}, {'id': 'lv', 'type': 'bus'}],
             'edges': [{'id': 'T', 'type': 'transformer', 'source': 'hv', 'target': 'lv', 'hv_kv': 132, 'lv_kv': 33}]}
    assert by_id(enrich(graph))['lv']['voltage_kv'] is None


def test_winding_ohms_can_be_referred_to_LV_without_changing_per_unit():
    graph = {'nodes': [{'id': 'T', 'type': 'transformer', 'impedance_voltage_kv': 33,
                        'windings': [{'voltage_kv': 132, 'r_ohm': 1, 'x_ohm': 10},
                                     {'voltage_kv': 33, 'r_ohm': .1, 'x_ohm': 1}]}], 'edges': []}
    transformer = by_id(enrich(graph, layout='incidence'))['T']
    assert transformer['x_pu'] == pytest.approx(10 * 100 / 132**2 + 1 * 100 / 33**2)
    assert transformer['x_ohm'] == pytest.approx(10 * (33/132)**2 + 1)
    assert transformer['impedance_voltage_kv'] == 33


def test_missing_winding_resistance_still_allows_known_reactance():
    graph = {'nodes': [{'id': 'T', 'type': 'transformer',
                        'windings': [{'voltage_kv': 132, 'x_ohm': 10}, {'voltage_kv': 33, 'x_ohm': 1}]}], 'edges': []}
    transformer = by_id(enrich(graph, layout='incidence'))['T']
    assert transformer['x_pu'] == pytest.approx(10 * 100 / 132**2 + 1 * 100 / 33**2)
    assert transformer['r_pu'] is None
    assert transformer['issues']


def test_transformer_supplied_per_unit_is_rebased_and_multiterminal_is_unresolved():
    graph = {'base_mva': 50, 'nodes': [{'id': 'T', 'type': 'transformer', 'hv_kv': 132, 'lv_kv': 33, 'x_pu': .1}], 'edges': []}
    assert by_id(enrich(graph, base=100))['T']['x_pu'] == pytest.approx(.2)
    graph['nodes'][0]['windings'] = [{'voltage_kv': v} for v in [132, 33, 11]]
    assert by_id(enrich(graph, base=100))['T']['x_pu'] is None


def test_unknown_active_state_blocks_propagation():
    graph = {'nodes': [{'id': 'a', 'voltage_kv': 132}, {'id': 'b'}],
             'edges': [{'id': 'line', 'source': 'a', 'target': 'b', 'in_service': None}]}
    result = enrich(graph)
    assert by_id(result)['b']['voltage_kv'] is None
    assert by_id(result, 'edges')['line']['in_service'] is None


def test_malformed_and_inconsistent_values_are_reported():
    graph = radial()
    graph['edges'][0].update(x_ohm=10, x_ohm_per_km=1, length_km=5)
    graph['nodes'][1]['load_mw'] = -10
    result = enrich(graph)
    assert by_id(result)['p1']['load_mw'] is None
    assert by_id(result)['p1']['issues']
    assert by_id(result, 'edges')['a']['x_ohm'] == 10  # preserve explicit source value
    assert by_id(result, 'edges')['a']['issues']


def test_demand_allocation_conserves_total_and_preserves_known_loads():
    graph = {'nodes': [{'id': 'A', 'type': 'substation', 'load_mw': 20},
                       {'id': 'B', 'type': 'substation'}, {'id': 'C', 'type': 'substation'}], 'edges': []}
    nodes = by_id(enrich(graph, {'total_load_mw': 100, 'demand_weights': {'A': 1, 'B': 1, 'C': 3}}))
    assert nodes['A']['load_mw'] == 20
    assert nodes['B']['load_mw'] == 20
    assert nodes['C']['load_mw'] == 60
    assert sum(n['load_mw'] for n in nodes.values()) == 100
    with pytest.raises(ValueError): enrich(graph, {'total_load_mw': 10, 'demand_weights': {'B': 1}})
    with pytest.raises(ValueError): enrich(graph, {'total_load_mw': 100, 'demand_weights': {'unknown': 1}})


def test_external_line_type_and_field_mapping_are_portable():
    graph = {'vertices': [{'key': 'A', 'category': 'mast', 'nominal_kV': 132}, {'key': 'B', 'category': 'mast'}],
             'connections': [{'key': 'wire', 'left': 'A', 'right': 'B', 'line_type': 'known-wire', 'distance': 10}]}
    mapping = {'nodes_key': 'vertices', 'edges_key': 'connections',
               'node_fields': {'id': 'key', 'kind': 'category', 'voltage_kv': 'nominal_kV'},
               'edge_fields': {'id': 'key', 'from': 'left', 'to': 'right', 'length_km': 'distance'}}
    result = enrich(graph, {'type_aliases': {'mast': 'pylon'},
                            'line_types': {'known-wire': {'x_ohm_per_km': .3, 'max_i_ka': .8}}}, mapping=mapping)
    assert result['vertices'][1]['electrical']['voltage_kv'] == 132
    assert result['connections'][0]['electrical']['x_ohm'] == 3
    assert result['connections'][0]['electrical']['provenance']['x_ohm_per_km']['method'] == 'explicit_line_type_assumption'


def test_sqlite_sidecar_streaming_and_source_filter(tmp_path):
    source = tmp_path / 'graph.sqlite'
    db = sqlite3.connect(source)
    db.executescript('''CREATE TABLE nodes(node_id TEXT,node_type TEXT,source TEXT,voltage_kv REAL,properties_json TEXT);
        CREATE TABLE edges(from_node TEXT,to_node TEXT,source TEXT,relation TEXT,evidence TEXT);
        CREATE TABLE relationships(from_node TEXT,to_node TEXT,source TEXT,relation TEXT,evidence TEXT);''')
    db.executemany('INSERT INTO nodes VALUES(?,?,?,?,?)', [('L', 'power_line', 'OSM', 400, '{"voltage":"400000;132000"}'),
        ('P', 'tower', 'OSM', None, '{}'), ('J', 'junction', 'OTHER', 11, '{}')])
    db.execute("INSERT INTO relationships VALUES('P','L','OSM','supports','mapped')")
    db.commit();db.close()
    original = source.read_bytes()
    engine = Enricher(tmp_path / 'output.sqlite', layout='incidence')
    try:
        import_sqlite(engine, source, {}, source='OSM')
        engine.propagate();report = engine.derive()
        row = engine.db.execute("SELECT * FROM node_electrical WHERE node_id='P'").fetchone()
        assert json.loads(row['voltage_levels_kv']) == [132, 400]
        assert row['voltage_kv'] is None
        assert report['counts']['nodes'] == 2
    finally:
        engine.db.close()
    assert source.read_bytes() == original


def test_duplicate_and_dangling_ids_are_rejected():
    with pytest.raises(ValueError, match='Duplicate'):
        enrich({'nodes': [{'id': 1}, {'id': '1'}], 'edges': []})
    with pytest.raises(ValueError, match='unknown endpoints'):
        enrich({'nodes': [{'id': 1}], 'edges': [{'source': 1, 'target': 2}]})


@pytest.mark.parametrize('assumptions', [{'power_factor': 1.1}, {'dispatch_fraction': -1}, {'total_load_mw': 20},
    {'demand_weights': {}}, {'line_types': {'bad': {'guess': 2}}}, {'nominal_taps': 'yes'}, {'invent_everything': True}])
def test_invalid_assumptions_rejected(assumptions):
    with pytest.raises(ValueError): validate_assumptions(assumptions)


@pytest.mark.parametrize('mapping', [{'unknown': 'field'}, {'node_fields': {'volts': 'voltage'}},
    {'node_fields': []}, {'edges_table': 1}, {'edge_fields': {'from': None}}])
def test_mapping_typos_fail_instead_of_silently_ignoring_fields(mapping):
    with pytest.raises(ValueError): validate_mapping(mapping)


def test_standalone_cli_preserves_input_and_refuses_overwrite(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'scripts/enrich_network_electrical.py'
    source, output = tmp_path / 'graph.json', tmp_path / 'result.json'
    source.write_text(json.dumps(radial()))
    original = source.read_bytes()
    result = subprocess.run([sys.executable, str(script), str(source), '--output', str(output), '--strict'], capture_output=True, text=True)
    assert result.returncode == 2  # unresolved line parameters, but useful output and report are written
    assert by_id(json.loads(output.read_text()))['p1']['voltage_kv'] == 132
    assert source.read_bytes() == original
    before = output.read_bytes()
    result = subprocess.run([sys.executable, str(script), str(source), '--output', str(output)], capture_output=True, text=True)
    assert result.returncode != 0 and output.read_bytes() == before


def test_cli_defaults_to_averaging_and_records_policy(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'scripts/enrich_network_electrical.py'
    source = tmp_path / 'source.json'
    source.write_text(json.dumps({'nodes': [{'id': 'a', 'voltage_kv': 132},
        {'id': 'b', 'voltage_kv': 400}, {'id': 'missing'}],
        'edges': [{'source': 'a', 'target': 'missing'}, {'source': 'b', 'target': 'missing'}]}))
    for policy, expected in [('neighbour-average', 266), ('none', None)]:
        output = tmp_path / f'{policy}.json'
        command = [sys.executable, str(script), str(source), '--output', str(output)]
        if policy == 'none': command += ['--voltage-fallback', 'none']
        completed = subprocess.run(command, capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
        result = json.loads(output.read_text())
        assert by_id(result)['missing']['voltage_kv'] == expected
        assert result['electrical_enrichment']['voltage_fallback'] == policy
