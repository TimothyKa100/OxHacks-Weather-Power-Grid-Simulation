#!/usr/bin/env python3
"""Portable, provenance-preserving electrical graph enrichment (Python stdlib only).

JSON: nodes/edges (or NetworkX nodes/links), equipment incidence graphs, and
the repository's buses/branches/generators format. SQLite: nodes, edges and
optional relationships tables. Never edits the input. See docs/NETWORK_ENRICHMENT.md.
"""
import argparse
from array import array
from collections import Counter
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import sys
import tempfile

VERSION = 'electrical-enrichment-v1'
SUPPORTS = {'tower', 'pylon', 'pole', 'support', 'gantry', 'cross_arm', 'insulator'}
TRANSFORMERS = {'transformer', 'power_transformer'}
SWITCHES = {'switch', 'breaker', 'disconnector', 'switchgear'}
LINES = {'power_line', 'line', 'cable', 'overhead_line', 'equivalent_branch'}
GENERATORS = {'generation', 'generator', 'power_plant', 'plant', 'distributed_generator', 'generator_machine'}
PASSIVE = SUPPORTS | LINES | TRANSFORMERS | SWITCHES | {'junction', 'terminal', 'busbar'}
ELECTRICAL = {'electrical_connection', 'electrical_terminal', 'line_terminal', 'transformer_terminal'}
SUPPORT_RELATIONS = {'supports', 'supports_line', 'route_support_or_equipment_member', 'supports_or_contains_terminal'}
FIELDS = {
    'voltage_kv': ['voltage_kv', 'kv', 'vn_kv'],
    'load_mw': ['load_mw', 'demand_mw', 'EnergyConsumer.p'],
    'generation_capacity_mw': ['generation_capacity_mw', 'max_mw', 'GeneratingUnit.ratedNetMaxP', 'RotatingMachine.maxExportP'],
    'generation_mw': ['generation_mw', 'base_mw'],
    'rating_mva': ['rating_mva', 'capacity_mva', 'thermal_rating_mva'],
    'unit_rating_mva': ['unit_rating_mva', 'sn_mva'],
    'rating_mw': ['rating_mw'], 'max_i_ka': ['max_i_ka'],
    'length_km': ['length_km', 'Conductor.length'],
    'r_ohm': ['r_ohm', 'ACLineSegment.r'], 'x_ohm': ['x_ohm', 'ACLineSegment.x'],
    'r_pu': ['r_pu'], 'x_pu': ['x_pu'],
    'r_ohm_per_km': ['r_ohm_per_km'], 'x_ohm_per_km': ['x_ohm_per_km'],
    'parallel': ['parallel'], 'derating_factor': ['derating_factor', 'df'],
    'power_factor': ['power_factor'], 'tap': ['tap', 'tap_pu'],
    'hv_kv': ['hv_kv', 'vn_hv_kv'], 'lv_kv': ['lv_kv', 'vn_lv_kv'],
    'vk_percent': ['vk_percent'], 'vkr_percent': ['vkr_percent'],
    'impedance_base_mva': ['impedance_base_mva'], 'impedance_voltage_kv': ['impedance_voltage_kv'],
    'from_voltage_kv': ['from_voltage_kv'], 'to_voltage_kv': ['to_voltage_kv'],
}
RESULT_FIELDS = ['voltage_kv', 'load_mw', 'generation_capacity_mw', 'generation_mw',
                 'r_ohm', 'x_ohm', 'r_pu', 'x_pu', 'rating_mva', 'rating_mw',
                 'length_km', 'max_i_ka', 'tap', 'impedance_voltage_kv']


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def number(value, name, minimum=0, positive=False):
    if isinstance(value, bool):
        raise ValueError(f'{name} must be numeric, not boolean')
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f'{name} must be numeric') from None
    if not math.isfinite(result) or result < minimum or (positive and result == 0):
        raise ValueError(f'{name} is outside its valid range')
    return result


def flag(value):
    if value is True or value == 1 or str(value).lower() in ('true', '1', 'yes', 'closed', 'in_service'):
        return True
    if value is False or value == 0 or str(value).lower() in ('false', '0', 'no', 'open', 'out_of_service'):
        return False
    return None


def identifier(value):
    if value is None or isinstance(value, (bool, dict, list)) or not str(value).strip():
        raise ValueError('Every node/edge needs a nonempty string or numeric ID')
    return str(value)


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def levels(value, scale=1):
    values = value if isinstance(value, list) else str(value).split(';')
    return sorted({number(v, 'nominal voltage', positive=True) * scale for v in values})


def properties(record):
    data = {}
    for key in ('properties_json', 'properties', 'attributes'):
        item = record.get(key)
        if isinstance(item, str):
            item = json.loads(item)
        if isinstance(item, dict):
            data.update(item)
    data.update({k: v for k, v in record.items() if v is not None})
    return data


def mapped(record, mapping, field, aliases, default=None):
    names = [mapping[field]] if field in mapping else aliases
    for name in names:
        if record.get(name) is not None:
            return record[name]
    return default


def normalize(record, field_map, kind_default, assumptions, input_base):
    """Read explicit units and CIM/OSM aliases; do not interpret generic 'voltage' as kV."""
    data = properties(record)
    kind = str(mapped(data, field_map, 'kind', ['node_type', 'kind', 'type', 'power'], kind_default)).lower()
    kind = assumptions.get('type_aliases', {}).get(kind, kind)
    source = str(mapped(data, field_map, 'layer', ['topology_layer', 'source'], 'input'))
    attrs, provenance, issues = {}, {}, []
    for field, aliases in FIELDS.items():
        raw = mapped(data, field_map, field, aliases)
        if raw is None:
            continue
        if field == 'voltage_kv':
            continue
        try:
            attrs[field] = number(raw, field, positive=field in {
                'parallel', 'tap', 'hv_kv', 'lv_kv', 'impedance_base_mva', 'impedance_voltage_kv',
                'from_voltage_kv', 'to_voltage_kv'})
            if field == 'parallel' and attrs[field] != int(attrs[field]):
                raise ValueError('parallel must count whole identical circuits')
            if field in ('derating_factor', 'power_factor') and not 0 < attrs[field] <= 1:
                raise ValueError(f'{field} must be in (0, 1]')
            source_field = field_map[field] if field in field_map else next(k for k in aliases if data.get(k) is not None)
            provenance[field] = {'method': 'provided', 'field': source_field}
        except ValueError as error:
            attrs.pop(field, None)
            provenance[field] = {'method': 'invalid_input'}
            issues.append(str(error))
    voltage = set()
    try:
        raw = mapped(data, field_map, 'voltage_kv', FIELDS['voltage_kv'])
        if raw is not None:
            voltage.update(levels(raw))
        for key in ('voltage_levels_kv', 'original_voltage_values_kv'):
            if data.get(key) is not None:
                voltage.update(levels(data[key]))
        # OSM voltage tags are volts, including multi-circuit lists such as 400000;132000.
        if data.get('voltage') is not None and (source.startswith('OSM') or 'power' in data):
            voltage.update(levels(data['voltage'], .001))
    except ValueError as error:
        voltage.clear()
        attrs['invalid_voltage'] = True
        issues.append(str(error))
    attrs['voltage_levels_kv'] = sorted(voltage)
    if voltage:
        provenance['voltage_kv'] = {'method': 'provided_nominal_voltage'}
    state_field = field_map.get('in_service', 'in_service')
    attrs['in_service'] = flag(record.get(state_field, data.get(state_field, True)))
    provenance['in_service'] = {'method': 'provided' if state_field in record or state_field in data else 'input_default_in_service'}
    if attrs['in_service'] is None:
        issues.append('Unknown in_service state; propagation blocked')
    if kind in SWITCHES:
        closed = mapped(data, field_map, 'closed', ['closed'])
        if closed is not None:
            attrs['closed'] = flag(closed)
        elif 'open' in data:
            opened = flag(data['open'])
            attrs['closed'] = None if opened is None else not opened
        elif 'Switch.normalOpen' in data:
            opened = flag(data['Switch.normalOpen'])
            attrs['closed'] = None if opened is None else not opened
            provenance['closed'] = {'method': 'published_normal_state_not_live_switch_state'}
        else:
            attrs['closed'] = None
        if attrs['closed'] is None:
            issues.append('Unknown switch state; propagation blocked')
    for key in ('line_type', 'circuit_id', 'windings', 'hv_bus', 'lv_bus', 'terminals'):
        if key in data:
            attrs[key] = data[key]
    template = assumptions.get('line_types', {}).get(attrs.get('line_type'))
    if template is not None and kind in LINES:
        for key, value in template.items():
            if key not in attrs:
                attrs[key] = value
                provenance[key] = {'method': 'explicit_line_type_assumption', 'line_type': attrs['line_type']}
    if input_base is not None and 'impedance_base_mva' not in attrs and any(k in attrs for k in ('r_pu', 'x_pu')):
        attrs['impedance_base_mva'] = input_base
        provenance['impedance_base_mva'] = {'method': 'input_network_base_mva'}
    return kind, source, attrs, provenance, issues


def validate_assumptions(value):
    if not isinstance(value, dict):
        raise ValueError('Assumptions must be a JSON object')
    unknown = set(value) - {'power_factor', 'dispatch_fraction', 'total_load_mw', 'demand_weights', 'line_types', 'nominal_taps', 'type_aliases'}
    if unknown:
        raise ValueError('Unknown assumptions: ' + ', '.join(sorted(unknown)))
    result = dict(value)
    for key in ('power_factor', 'dispatch_fraction', 'total_load_mw'):
        if key in value:
            result[key] = number(value[key], key, positive=key == 'power_factor')
            if key != 'total_load_mw' and result[key] > 1:
                raise ValueError(f'{key} must be <= 1')
    if ('total_load_mw' in value) != ('demand_weights' in value):
        raise ValueError('Supply total_load_mw and explicit demand_weights together')
    if 'demand_weights' in value:
        if not isinstance(value['demand_weights'], dict) or not value['demand_weights']:
            raise ValueError('demand_weights must map node IDs to positive weights')
        result['demand_weights'] = {identifier(k): number(v, 'demand weight', positive=True) for k, v in value['demand_weights'].items()}
    if 'nominal_taps' in value and not isinstance(value['nominal_taps'], bool):
        raise ValueError('nominal_taps must be boolean')
    if not isinstance(value.get('line_types', {}), dict) or not isinstance(value.get('type_aliases', {}), dict):
        raise ValueError('line_types and type_aliases must be objects')
    if any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in value.get('type_aliases', {}).items()):
        raise ValueError('type_aliases must map type names to nonempty strings')
    if 'type_aliases' in result:
        result['type_aliases'] = {k.lower(): v.lower() for k, v in result['type_aliases'].items()}
    for name, template in value.get('line_types', {}).items():
        if not isinstance(template, dict) or set(template) - {'r_ohm_per_km', 'x_ohm_per_km', 'max_i_ka'}:
            raise ValueError('Line types allow r_ohm_per_km, x_ohm_per_km and max_i_ka only')
        result.setdefault('line_types', {})[name] = {k: number(v, k) for k, v in template.items()}
    return result


def validate_mapping(value):
    if not isinstance(value, dict):
        raise ValueError('Mapping must be a JSON object')
    allowed = {'nodes_key', 'edges_key', 'nodes_table', 'edges_table', 'relationships_table', 'node_fields', 'edge_fields'}
    if set(value) - allowed:
        raise ValueError('Unknown mapping keys: ' + ', '.join(sorted(set(value) - allowed)))
    for key, entry in value.items():
        if key in ('node_fields', 'edge_fields'):
            fields = set(FIELDS) | {'id', 'kind', 'layer', 'in_service', 'closed'}
            if key == 'edge_fields':
                fields |= {'from', 'to'}
            if not isinstance(entry, dict) or set(entry) - fields or any(not isinstance(v, str) or not v for v in entry.values()):
                raise ValueError(f'{key} must map supported canonical fields to input field names')
        elif not isinstance(entry, str) or not entry:
            raise ValueError(f'{key} must be a nonempty string')
    return value


class UnionFind:
    """Five bytes per entity, rather than a Python object per national-grid node."""
    def __init__(self, count):
        self.parent = array('I', range(count + 1))
        self.rank = bytearray(count + 1)

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a == b:
            return
        if self.rank[a] < self.rank[b]:
            a, b = b, a
        self.parent[b] = a
        if self.rank[a] == self.rank[b]:
            self.rank[a] += 1


class Enricher:
    """Disk-backed normalized graph. JSON adapters and SQLite adapters share the rules."""
    def __init__(self, output, base_mva=100, assumptions=None, layout='node-link', voltage_fallback='neighbour-average'):
        self.db = sqlite3.connect(output)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA temp_store=FILE')
        self.db.execute('PRAGMA cache_size=-32768')
        self.base = number(base_mva, 'base_mva', positive=True)
        self.assumptions = validate_assumptions(assumptions or {})
        self.layout = layout
        if voltage_fallback not in ('neighbour-average', 'none'):
            raise ValueError('voltage_fallback must be neighbour-average or none')
        self.voltage_fallback = voltage_fallback
        self.counts = Counter()
        self.db.executescript('''
            CREATE TABLE entities(i INTEGER PRIMARY KEY, entity_type TEXT, id TEXT, kind TEXT, layer TEXT,
                attrs TEXT, provenance TEXT, issues TEXT, barrier INTEGER, component INTEGER,
                electrical TEXT, UNIQUE(entity_type,id));
            CREATE TABLE links(a INTEGER,b INTEGER,relation TEXT,evidence TEXT,circuit_id TEXT);
            CREATE TABLE pending_links(a TEXT,b TEXT,relation TEXT,evidence TEXT,circuit_id TEXT);
            CREATE TABLE seeds(entity INTEGER,voltage REAL,origin TEXT);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
        ''')

    def add_entity(self, record, entity_type='node', mapping=None, default_kind='junction', input_base=None):
        mapping = mapping or {}
        data = properties(record)
        ident = identifier(mapped(data, mapping, 'id', ['node_id', 'id']))
        kind, layer, attrs, provenance, issues = normalize(record, mapping, default_kind, self.assumptions, input_base)
        barrier = (kind in TRANSFORMERS | {'converter', 'inverter'}
                   or attrs['in_service'] is not True or attrs.get('invalid_voltage') or len(attrs['voltage_levels_kv']) > 1
                   or kind in SWITCHES and attrs.get('closed') is not True
                   or kind in SUPPORTS and self.layout == 'incidence')
        try:
            row = self.db.execute('INSERT INTO entities(entity_type,id,kind,layer,attrs,provenance,issues,barrier) VALUES(?,?,?,?,?,?,?,?)',
                                  (entity_type, ident, kind, layer, encode(attrs), encode(provenance), encode(issues), int(barrier)))
        except sqlite3.IntegrityError:
            raise ValueError(f'Duplicate {entity_type} ID: {ident}') from None
        index = row.lastrowid
        # Multi-voltage equipment remains a boundary, not a scalar voltage anchor.
        if len(attrs['voltage_levels_kv']) == 1:
            self.db.execute('INSERT INTO seeds VALUES(?,?,?)', (index, attrs['voltage_levels_kv'][0], ident))
        self.counts[entity_type + 's'] += 1
        return index

    def add_link(self, a, b, relation='electrical_connection', evidence='input_declared_connectivity', circuit_id=None):
        self.db.execute('INSERT INTO pending_links VALUES(?,?,?,?,?)',
                        (identifier(a), identifier(b), relation, evidence, None if circuit_id is None else str(circuit_id)))

    def resolve_links(self, allow_filtered=False):
        self.db.execute('CREATE INDEX entities_identity ON entities(id,entity_type)')
        missing = self.db.execute('''SELECT p.a,p.b FROM pending_links p
            LEFT JOIN entities a ON a.id=p.a AND a.entity_type='node'
            LEFT JOIN entities b ON b.id=p.b AND b.entity_type='node'
            WHERE a.i IS NULL OR b.i IS NULL LIMIT 1''').fetchone()
        if missing and not allow_filtered:
            raise ValueError(f'Dangling graph endpoint: {tuple(missing)}')
        self.counts['filtered_or_dangling_links'] = self.db.execute('''SELECT COUNT(*) FROM pending_links p
            LEFT JOIN entities a ON a.id=p.a AND a.entity_type='node'
            LEFT JOIN entities b ON b.id=p.b AND b.entity_type='node'
            WHERE a.i IS NULL OR b.i IS NULL''').fetchone()[0]
        self.db.execute('''INSERT INTO links SELECT a.i,b.i,p.relation,p.evidence,p.circuit_id FROM pending_links p
            JOIN entities a ON a.id=p.a AND a.entity_type='node'
            JOIN entities b ON b.id=p.b AND b.entity_type='node' ''')
        self.db.execute('DROP TABLE pending_links')
        self.db.execute('CREATE INDEX links_a ON links(a)')
        self.db.execute('CREATE INDEX links_b ON links(b)')
        self.seed_transformer_terminals()
        self.db.commit()

    def seed_transformer_terminals(self):
        for row in self.db.execute("SELECT i,id,layer,attrs FROM entities WHERE kind IN ('transformer','power_transformer')"):
            attrs = json.loads(row['attrs'])
            terminals = attrs.get('terminals', []) + [w for w in attrs.get('windings', []) if 'node_id' in w]
            for terminal in terminals:
                ident = identifier(terminal.get('node_id'))
                voltage = number(terminal.get('voltage_kv', terminal.get('PowerTransformerEnd.ratedU')), 'transformer terminal voltage', positive=True)
                node = self.db.execute("SELECT i,layer FROM entities WHERE entity_type='node' AND id=?", (ident,)).fetchone()
                if node is None or node['layer'] != row['layer']:
                    raise ValueError(f'Transformer terminal {ident} is absent or in another source layer')
                links = self.db.execute('SELECT relation FROM links WHERE (a=? AND b=?) OR (a=? AND b=?)',
                                        (row['i'], node['i'], node['i'], row['i'])).fetchall()
                if not any(link['relation'] in ELECTRICAL for link in links):
                    raise ValueError(f'Transformer terminal {ident} needs an explicit electrical connection')
                self.db.execute('INSERT INTO seeds VALUES(?,?,?)', (node['i'], voltage, f'{row["id"]}:terminal:{ident}'))

    def add_branches(self, records, mapping=None, input_base=None):
        mapping = mapping or {}
        for n, record in enumerate(records):
            data = properties(record)
            a = identifier(mapped(data, mapping, 'from', ['from', 'from_node', 'source', 'from_bus']))
            b = identifier(mapped(data, mapping, 'to', ['to', 'to_node', 'target', 'to_bus']))
            endpoints = [self.db.execute("SELECT i,layer FROM entities WHERE entity_type='node' AND id=?", (item,)).fetchone() for item in (a, b)]
            if any(item is None for item in endpoints):
                raise ValueError(f'Branch has unknown endpoints: {a}, {b}')
            relation = str(data.get('relation', 'electrical_connection'))
            if relation not in ELECTRICAL:
                self.add_link(a, b, relation, str(data.get('evidence', 'input_relationship')))
                continue
            row = dict(record)
            if mapped(data, mapping, 'id', ['node_id', 'id']) is None:
                row[mapping.get('id', 'id')] = f'edge:{n}'
            # Node-link "source" is an endpoint, never a provenance layer.
            row['topology_layer'] = data.get('topology_layer', endpoints[0]['layer'])
            try:
                nonnominal_tap = number(mapped(data, mapping, 'tap', ['tap', 'tap_pu'], 1), 'tap', positive=True) != 1
            except ValueError:
                nonnominal_tap = False  # invalid tap will be reported by normalization
            default_kind = 'transformer' if (any(k in data for k in ('hv_kv', 'lv_kv', 'vn_hv_kv', 'vn_lv_kv'))
                                             or nonnominal_tap) else 'power_line'
            edge = self.add_entity(row, 'edge', mapping, default_kind, input_base)
            edge_data = self.db.execute('SELECT attrs,kind FROM entities WHERE i=?', (edge,)).fetchone()
            attrs = json.loads(edge_data['attrs'])
            attrs['from_node'], attrs['to_node'] = a, b
            if edge_data['kind'] in TRANSFORMERS:
                for key in ('hv_bus', 'lv_bus'):
                    if key in attrs and str(attrs[key]) not in (a, b):
                        raise ValueError(f'{key} must identify one of this transformer’s endpoints')
                if 'hv_bus' in attrs and 'lv_bus' in attrs and str(attrs['hv_bus']) == str(attrs['lv_bus']):
                    raise ValueError('HV and LV terminal mappings must reference distinct nodes')
                # Only explicit winding/endpoint associations seed a transformer side.
                for node_id, side in [(a, 'from'), (b, 'to')]:
                    voltages = {attrs.get(side + '_voltage_kv')}
                    if str(attrs.get('hv_bus')) == node_id:
                        voltages.add(attrs.get('hv_kv'))
                    if str(attrs.get('lv_bus')) == node_id:
                        voltages.add(attrs.get('lv_kv'))
                    for voltage in voltages - {None}:
                        index = endpoints[0]['i'] if node_id == a else endpoints[1]['i']
                        self.db.execute('INSERT INTO seeds VALUES(?,?,?)', (index, voltage, f'transformer:{edge}:{side}'))
            self.db.execute('UPDATE entities SET attrs=? WHERE i=?', (encode(attrs), edge))
            for endpoint in endpoints:
                self.db.execute('INSERT INTO links VALUES(?,?,?,?,?)',
                                (edge, endpoint['i'], relation, str(data.get('evidence', 'input_declared_connectivity')), attrs.get('circuit_id')))

    def add_generators(self, records):
        grouped = {}
        for generator in records:
            bus = identifier(generator.get('bus'))
            if not self.db.execute("SELECT 1 FROM entities WHERE entity_type='node' AND id=?", (bus,)).fetchone():
                raise ValueError(f'Unknown generator bus: {bus}')
            totals = grouped.setdefault(bus, {'generation_capacity_mw': 0., 'generation_mw': 0.})
            for target, key in [('generation_capacity_mw', 'max_mw'), ('generation_mw', 'base_mw')]:
                if generator.get(key) is None:
                    totals[target] = None
                elif totals[target] is not None:
                    totals[target] += number(generator[key], key)
        for bus, totals in grouped.items():
            row = self.db.execute("SELECT i,attrs,provenance FROM entities WHERE entity_type='node' AND id=?", (bus,)).fetchone()
            attrs, provenance = json.loads(row['attrs']), json.loads(row['provenance'])
            for key, value in totals.items():
                if key in attrs and value is not None and not math.isclose(attrs[key], value, rel_tol=1e-6):
                    raise ValueError(f'Conflicting node and generator totals for {bus}: {key}')
                if key not in attrs and value is not None:
                    attrs[key] = value
                    provenance[key] = {'method': 'sum_attached_generator_records', 'bus': bus}
            attrs['has_generator_records'] = True
            self.db.execute('UPDATE entities SET attrs=?,provenance=? WHERE i=?', (encode(attrs), encode(provenance), row['i']))
        # Only a supplied generator array makes absence meaningful. It is the
        # declared complete generator list of a bus/branch electrical model.
        for row in self.db.execute("SELECT i,attrs,provenance FROM entities WHERE entity_type='node' AND kind IN ('bus','junction')"):
            attrs, provenance = json.loads(row['attrs']), json.loads(row['provenance'])
            if not attrs.get('has_generator_records'):
                for key in ('generation_capacity_mw', 'generation_mw'):
                    if key not in attrs:
                        attrs[key] = 0.
                        provenance[key] = {'method': 'absent_from_explicit_complete_generator_list'}
                self.db.execute('UPDATE entities SET attrs=?,provenance=? WHERE i=?', (encode(attrs), encode(provenance), row['i']))

    def propagate(self):
        """Union voltage-compatible topology, then resolve all anchors together.

        Conflicting anchors quarantine inference for the entire component, so
        results never depend on BFS ordering or on the nearest power plant.
        """
        count = self.db.execute('SELECT COALESCE(MAX(i),0) FROM entities').fetchone()[0]
        union = UnionFind(count)
        # Explicit multiple circuits on a shared support require circuit-specific ports.
        for row in self.db.execute('''SELECT e.i,COUNT(DISTINCT l.circuit_id) circuits FROM entities e
            JOIN links l ON e.i=l.a OR e.i=l.b
            WHERE e.kind IN ('tower','pylon','pole','support') GROUP BY e.i HAVING circuits>1'''):
            self.db.execute('UPDATE entities SET barrier=1 WHERE i=?', (row['i'],))
        query = '''SELECT l.*,a.layer a_layer,b.layer b_layer,a.barrier a_barrier,b.barrier b_barrier
            FROM links l JOIN entities a ON a.i=l.a JOIN entities b ON b.i=l.b'''
        for row in self.db.execute(query):
            if row['relation'] not in ELECTRICAL:
                self.counts['nonconducting_relationships'] += 1
            elif row['a_layer'] != row['b_layer']:
                self.counts['cross_layer_connections_blocked'] += 1
            elif row['a_barrier'] or row['b_barrier']:
                self.counts['boundary_connections'] += 1
            else:
                union.union(row['a'], row['b'])
                self.counts['voltage_connections'] += 1
        self.db.executemany('UPDATE entities SET component=? WHERE i=?', ((union.find(i), i) for i in range(1, count + 1)))
        del union
        self.db.execute('CREATE INDEX entity_component ON entities(component)')
        self.db.execute('''CREATE TABLE components AS SELECT e.component,
            MIN(s.voltage) min_voltage, MAX(s.voltage) max_voltage,COUNT(s.voltage) anchors,
            MIN(s.origin) example_origin FROM entities e LEFT JOIN seeds s ON e.i=s.entity GROUP BY e.component''')
        self.db.execute('CREATE UNIQUE INDEX component_id ON components(component)')
        # Tight relative tolerance only for roundoff. Never blend different nominal classes.
        self.db.execute('ALTER TABLE components ADD COLUMN conflict INTEGER DEFAULT 0')
        self.db.execute('UPDATE components SET conflict=1 WHERE max_voltage-min_voltage>MAX(0.000001,max_voltage*0.000001)')
        self.counts['voltage_conflict_components'] = self.db.execute('SELECT COUNT(*) FROM components WHERE conflict=1').fetchone()[0]
        self.db.commit()

    def allocate_demand(self):
        if 'total_load_mw' not in self.assumptions:
            return
        total = self.assumptions['total_load_mw']
        known = 0.
        eligible = []
        weights = self.assumptions['demand_weights']
        for ident, weight in weights.items():
            row = self.db.execute("SELECT * FROM entities WHERE entity_type='node' AND id=?", (ident,)).fetchone()
            if row is None:
                raise ValueError(f'Demand weight references unknown node {ident}')
            if row['kind'] in PASSIVE:
                raise ValueError(f'Demand weights cannot allocate local demand to passive equipment: {ident}')
            if json.loads(row['attrs']).get('load_mw') is None:
                eligible.append((row, weight))
        # Total is for this selected graph, including existing loads outside the weighted set.
        for row in self.db.execute("SELECT attrs FROM entities WHERE entity_type='node'"):
            known += json.loads(row['attrs']).get('load_mw', 0.)
        remaining = total - known
        if remaining < -1e-8 or (not eligible and abs(remaining) > 1e-8):
            raise ValueError('total_load_mw cannot be allocated while preserving supplied loads')
        weight_sum = sum(weight for _, weight in eligible)
        assigned = 0.
        for position, (row, weight) in enumerate(eligible):
            value = remaining - assigned if position == len(eligible) - 1 else remaining * weight / weight_sum
            attrs, provenance = json.loads(row['attrs']), json.loads(row['provenance'])
            attrs['load_mw'] = max(0., value)
            assigned += value
            provenance['load_mw'] = {'method': 'explicit_total_and_weight_allocation', 'weight': weight, 'total_load_mw': total}
            self.db.execute('UPDATE entities SET attrs=?,provenance=? WHERE i=?', (encode(attrs), encode(provenance), row['i']))

    def derive(self):
        self.allocate_demand()
        cursor = self.db.execute('''SELECT e.*,c.min_voltage,c.max_voltage,c.anchors,c.example_origin,c.conflict
            FROM entities e JOIN components c ON e.component=c.component ORDER BY e.i''')
        while rows := cursor.fetchmany(5000):
            updates = []
            for row in rows:
                attrs, provenance, issues = (json.loads(row[key]) for key in ('attrs', 'provenance', 'issues'))
                result = derive_values(attrs, row['kind'], self.base, self.assumptions, provenance, issues)
                voltage = attrs['voltage_levels_kv']
                if len(voltage) == 1:
                    result['voltage_kv'] = voltage[0]
                elif not voltage and row['anchors'] and not row['conflict'] and not attrs.get('invalid_voltage'):
                    result['voltage_kv'] = row['min_voltage']
                    result['voltage_levels_kv'] = [row['min_voltage']]
                    provenance['voltage_kv'] = {'method': 'connected_nominal_voltage', 'component': row['component'],
                                               'anchor_example': row['example_origin'], 'anchor_count': row['anchors']}
                if row['conflict']:
                    issues.append('Conflicting nominal voltage anchors in connected component; physical voltage unresolved')
                # Voltage-dependent arithmetic follows resolution, not initial parsing.
                derive_impedance_and_rating(result, attrs, row['kind'], self.base, self.assumptions, provenance, issues)
                result.update(provenance=provenance, issues=issues, component=row['component'], voltage_conflict=bool(row['conflict']))
                updates.append((encode(result), row['i']))
            self.db.executemany('UPDATE entities SET electrical=? WHERE i=?', updates)
        self.db.commit()
        self.resolve_supports()
        if self.voltage_fallback == 'neighbour-average':
            self.average_unresolved_voltages()
        missing_counts = Counter()
        cursor = self.db.execute('SELECT i,kind,electrical FROM entities')
        while rows := cursor.fetchmany(5000):
            updates = []
            for row in rows:
                result = json.loads(row['electrical'])
                required = required_fields(row['kind'], result)
                if (result.get('generation_capacity_mw') or 0) > 0 and 'generation_mw' not in required:
                    required.append('generation_mw')
                result['missing_fields'] = [k for k in required if result.get(k) is None or k == 'voltage_levels_kv' and not result[k]]
                result['electrical_fields_complete'] = not result['missing_fields'] and not result['issues']
                missing_counts.update(result['missing_fields'])
                self.counts['records_with_issues'] += bool(result['issues'])
                self.counts['records_with_inferred_voltage'] += result['provenance'].get('voltage_kv', {}).get('method') in ('connected_nominal_voltage', 'carried_circuit_voltages', 'neighbour_average_estimate')
                updates.append((encode(result), row['i']))
            self.db.executemany('UPDATE entities SET electrical=? WHERE i=?', updates)
        self.report = {'version': VERSION, 'layout': self.layout, 'base_mva': self.base, 'voltage_fallback': self.voltage_fallback,
                       'counts': dict(self.counts), 'missing_fields': dict(missing_counts),
                       'assumptions': self.assumptions,
                       'cascade_ready': False,
                       'scope': 'Nominal electrical property enrichment; topology validation and power-flow operating point remain separate.'}
        self.db.execute('INSERT INTO metadata VALUES(?,?)', ('report', encode(self.report)))
        self.db.executescript('''
            CREATE VIEW node_electrical AS SELECT id node_id,kind node_type,layer source,
                json_extract(electrical,'$.voltage_kv') voltage_kv,
                json_extract(electrical,'$.voltage_levels_kv') voltage_levels_kv,
                json_extract(electrical,'$.load_mw') load_mw,
                json_extract(electrical,'$.generation_capacity_mw') generation_capacity_mw,
                json_extract(electrical,'$.generation_mw') generation_mw,
                json_extract(electrical,'$.r_ohm') r_ohm,json_extract(electrical,'$.x_ohm') x_ohm,
                json_extract(electrical,'$.x_pu') x_pu,json_extract(electrical,'$.rating_mw') rating_mw,
                electrical properties_json FROM entities WHERE entity_type='node';
            CREATE VIEW edge_electrical AS SELECT id edge_id,kind,layer source,
                json_extract(attrs,'$.from_node') from_node,json_extract(attrs,'$.to_node') to_node,
                electrical properties_json FROM entities WHERE entity_type='edge';
        ''')
        self.db.commit()
        return self.report

    def average_unresolved_voltages(self):
        """Synchronous frontier filling: average currently known direct neighbours.

        Each missing value is filled once, in rounds independent of row order.
        Electrical edges and explicit support links are eligible; source layers,
        transformer boundaries and open/unknown switches remain separate. Work
        is disk-backed and only visits the newly reached frontier each round.
        """
        self.db.executescript('''
            CREATE TEMP TABLE average_state(i INTEGER PRIMARY KEY,voltage REAL,filled_round INTEGER,eligible INTEGER);
            INSERT INTO average_state
            SELECT i,json_extract(electrical,'$.voltage_kv'),
                CASE WHEN json_extract(electrical,'$.voltage_kv') IS NOT NULL THEN 0 END,
                (kind NOT IN ('transformer','power_transformer','converter','inverter')
                 AND json_extract(attrs,'$.in_service')=1
                 AND COALESCE(json_extract(attrs,'$.invalid_voltage'),0)=0
                 AND json_array_length(json_extract(electrical,'$.voltage_levels_kv'))<=1
                 AND (kind NOT IN ('switch','breaker','disconnector','switchgear') OR json_extract(attrs,'$.closed')=1))
            FROM entities;
            CREATE INDEX temp.average_frontier ON average_state(filled_round,i);
            CREATE TEMP TABLE average_neighbours(a INTEGER,b INTEGER,priority INTEGER,PRIMARY KEY(a,b));
        ''')
        accepted = sorted(ELECTRICAL | SUPPORT_RELATIONS)
        placeholders = ','.join('?' for _ in accepted)
        # Ordinary JSON nodes are neighbours across a physical branch. That
        # branch's internal normalization entity must not bias their average.
        if self.layout == 'node-link':
            self.db.execute(f'''INSERT OR IGNORE INTO average_neighbours
                SELECT x.b,y.b,0 FROM links x JOIN links y ON x.a=y.a AND x.b<>y.b
                JOIN entities branch ON branch.i=x.a AND branch.entity_type='edge'
                JOIN average_state bs ON bs.i=branch.i AND bs.eligible=1
                JOIN entities a ON a.i=x.b JOIN entities b ON b.i=y.b
                JOIN average_state sa ON sa.i=a.i AND sa.eligible=1
                JOIN average_state sb ON sb.i=b.i AND sb.eligible=1
                WHERE a.layer=b.layer AND a.layer=branch.layer
                AND x.relation IN ({placeholders}) AND y.relation IN ({placeholders})''', [*accepted, *accepted])
        priority = 1 if self.layout == 'node-link' else 0
        self.db.execute(f'''INSERT OR IGNORE INTO average_neighbours
            SELECT l.a,l.b,? FROM links l JOIN entities a ON a.i=l.a JOIN entities b ON b.i=l.b
            JOIN average_state sa ON sa.i=a.i AND sa.eligible=1
            JOIN average_state sb ON sb.i=b.i AND sb.eligible=1
            WHERE a.layer=b.layer AND l.a<>l.b AND l.relation IN ({placeholders})''', [priority, *accepted])
        self.db.execute('INSERT OR IGNORE INTO average_neighbours SELECT b,a,priority FROM average_neighbours')
        self.db.executescript('''
            CREATE INDEX temp.average_donors ON average_neighbours(b,a);
            CREATE TEMP TABLE average_candidates(i INTEGER PRIMARY KEY);
            CREATE TEMP TABLE average_next(i INTEGER PRIMARY KEY,voltage REAL,neighbours INTEGER);
            CREATE TEMP TABLE average_evidence(i INTEGER PRIMARY KEY,filled_round INTEGER,neighbours INTEGER);
        ''')
        round_number = 0
        while True:
            self.db.execute('DELETE FROM average_candidates')
            self.db.execute('''INSERT OR IGNORE INTO average_candidates
                SELECT n.a FROM average_state donor JOIN average_neighbours n ON n.b=donor.i
                JOIN average_state target ON target.i=n.a
                WHERE donor.filled_round=? AND target.voltage IS NULL''', (round_number,))
            self.db.execute('DELETE FROM average_next')
            self.db.execute('''INSERT INTO average_next
                WITH means AS (
                    SELECT c.i,n.priority,AVG(d.voltage) voltage,COUNT(*) neighbours
                    FROM average_candidates c JOIN average_neighbours n ON n.a=c.i
                    JOIN average_state d ON d.i=n.b AND d.voltage IS NOT NULL
                    GROUP BY c.i,n.priority
                ) SELECT m.i,m.voltage,m.neighbours FROM means m
                  WHERE m.priority=(SELECT MIN(p.priority) FROM means p WHERE p.i=m.i)''')
            count = self.db.execute('SELECT COUNT(*) FROM average_next').fetchone()[0]
            if not count:
                break
            round_number += 1
            # All means used the previous snapshot. Updating only after computing
            # the entire frontier prevents iteration-order-dependent estimates.
            self.db.execute('''UPDATE average_state SET voltage=(SELECT voltage FROM average_next WHERE i=average_state.i),
                filled_round=? WHERE i IN (SELECT i FROM average_next)''', (round_number,))
            self.db.execute('INSERT INTO average_evidence SELECT i,?,neighbours FROM average_next', (round_number,))
            self.counts['voltage_average_estimates'] += count
        self.counts['voltage_average_rounds'] = round_number
        cursor = self.db.execute('''SELECT e.*,s.voltage,a.filled_round,a.neighbours FROM average_evidence a
            JOIN entities e ON e.i=a.i JOIN average_state s ON s.i=a.i''')
        while rows := cursor.fetchmany(5000):
            updates = []
            for row in rows:
                result, attrs = json.loads(row['electrical']), json.loads(row['attrs'])
                result['voltage_kv'] = row['voltage']
                result['voltage_levels_kv'] = [row['voltage']]
                result['voltage_is_estimate'] = True
                result['provenance']['voltage_kv'] = {
                    'method': 'neighbour_average_estimate', 'estimated': True,
                    'round': row['filled_round'], 'neighbour_count': row['neighbours'],
                    'rule': 'Arithmetic mean of known distinct neighbours before this round',
                }
                derive_impedance_and_rating(result, attrs, row['kind'], self.base, self.assumptions,
                                            result['provenance'], result['issues'])
                result['issues'] = list(dict.fromkeys(result['issues']))
                for field in ('x_pu', 'r_pu', 'rating_mva', 'rating_mw'):
                    if result.get(field) is not None and result['provenance'].get(field, {}).get('method') != 'provided':
                        result['provenance'][field]['uses_estimated_voltage'] = True
                updates.append((encode(result), row['i']))
            self.db.executemany('UPDATE entities SET electrical=? WHERE i=?', updates)
        self.db.executescript('''DROP TABLE average_evidence;DROP TABLE average_next;
            DROP TABLE average_candidates;DROP TABLE average_neighbours;DROP TABLE average_state;''')

    def resolve_supports(self):
        """Read circuit voltages onto mechanical supports without joining circuits."""
        query = '''SELECT e.* FROM entities e WHERE kind IN ('tower','pylon','pole','support','gantry','cross_arm','insulator')'''
        for row in self.db.execute(query):
            result = json.loads(row['electrical'])
            carried = set(result['voltage_levels_kv'])
            evidence = []
            for neighbour in self.db.execute('''SELECT n.id,n.kind,n.layer,n.electrical,l.relation FROM links l JOIN entities n
                ON n.i=CASE WHEN l.a=? THEN l.b ELSE l.a END WHERE l.a=? OR l.b=?''', (row['i'], row['i'], row['i'])):
                if neighbour['layer'] != row['layer'] or neighbour['relation'] not in SUPPORT_RELATIONS | ELECTRICAL:
                    continue
                if neighbour['kind'] in SUPPORTS | TRANSFORMERS | {'converter', 'inverter'}:
                    continue
                other = json.loads(neighbour['electrical'])
                # A neighbouring support is not evidence of a circuit's voltage.
                if other.get('voltage_conflict') or other.get('provenance', {}).get('voltage_kv', {}).get('method') == 'carried_circuit_voltages':
                    continue
                if other['voltage_levels_kv']:
                    carried.update(other['voltage_levels_kv'])
                    if len(evidence) < 8:
                        evidence.append(neighbour['id'])
            if carried:
                result['voltage_levels_kv'] = sorted(carried)
                result['voltage_kv'] = next(iter(carried)) if len(carried) == 1 else None
                if evidence:
                    result['provenance']['voltage_kv'] = {'method': 'carried_circuit_voltages', 'neighbour_examples': evidence,
                                                        'electrical_connection_created': False}
                self.db.execute('UPDATE entities SET electrical=? WHERE i=?', (encode(result), row['i']))


def derived(result, provenance, key, value, method, **details):
    if result.get(key) is None and value is not None and provenance.get(key, {}).get('method') != 'invalid_input':
        result[key] = value
        provenance[key] = {'method': method, **details}


def derive_values(attrs, kind, base, assumptions, provenance, issues):
    result = {key: attrs.get(key) for key in RESULT_FIELDS}
    result.update(voltage_levels_kv=list(attrs['voltage_levels_kv']), base_mva=base, in_service=attrs['in_service'])
    if kind in SWITCHES:
        result['closed'] = attrs.get('closed')
    if kind in PASSIVE:
        derived(result, provenance, 'load_mw', 0., 'passive_equipment_no_local_demand')
        if not attrs.get('has_generator_records'):
            derived(result, provenance, 'generation_capacity_mw', 0., 'passive_equipment_no_generation')
            derived(result, provenance, 'generation_mw', 0., 'passive_equipment_no_generation')
    if result['generation_mw'] is None and result['generation_capacity_mw'] is not None and 'dispatch_fraction' in assumptions:
        derived(result, provenance, 'generation_mw', result['generation_capacity_mw'] * assumptions['dispatch_fraction'],
                'explicit_dispatch_fraction_assumption', fraction=assumptions['dispatch_fraction'])
    if result['generation_mw'] is not None and result['generation_capacity_mw'] is not None and result['generation_mw'] > result['generation_capacity_mw']:
        issues.append('Generation output exceeds available capacity')
    if kind in LINES:
        derived(result, provenance, 'tap', 1., 'ordinary_line_no_transformer_tap')
    elif kind in TRANSFORMERS and assumptions.get('nominal_taps'):
        derived(result, provenance, 'tap', 1., 'explicit_nominal_tap_assumption')
    return result


def derive_impedance_and_rating(result, attrs, kind, base, assumptions, provenance, issues):
    voltage = result['voltage_kv']
    parallel = attrs.get('parallel', 1.)
    if kind in LINES:
        for key in ('r_ohm', 'x_ohm'):
            if attrs.get(key + '_per_km') is not None and result['length_km'] is not None:
                computed = attrs[key + '_per_km'] * result['length_km'] / parallel
                if result[key] is not None and not math.isclose(result[key], computed, rel_tol=1e-4, abs_tol=1e-8):
                    issues.append(f'{key} disagrees with per-km impedance, length and parallel circuits')
                derived(result, provenance, key, computed, 'per_km_times_length_divided_by_identical_parallel', parallel=parallel)
        if voltage:
            for axis in ('r', 'x'):
                ohm, pu = axis + '_ohm', axis + '_pu'
                old_base = attrs.get('impedance_base_mva')
                old_voltage = attrs.get('impedance_voltage_kv', voltage)
                supplied_pu = attrs.get(pu)
                if supplied_pu is not None:
                    if old_base is None:
                        result[pu] = None
                        issues.append(f'{pu} has no source impedance_base_mva; conversion unresolved')
                    else:
                        implied_ohm = supplied_pu * old_voltage ** 2 / old_base
                        if result[ohm] is not None and not math.isclose(result[ohm], implied_ohm, rel_tol=1e-4, abs_tol=1e-8):
                            issues.append(f'Inconsistent supplied {pu} and {ohm}')
                        derived(result, provenance, ohm, implied_ohm, 'source_per_unit_to_ohms', source_base_mva=old_base, source_voltage_kv=old_voltage)
                if result[ohm] is not None:
                    result[pu] = result[ohm] * base / voltage ** 2
                    provenance[pu] = {'method': 'ohms_to_system_per_unit', 'base_mva': base, 'voltage_base_kv': voltage}
            result['impedance_voltage_kv'] = voltage
        elif any(attrs.get(k) is not None for k in ('r_pu', 'x_pu')):
            # Existing per-unit values can be rebased in MVA without knowing voltage,
            # provided the voltage base stays unchanged and is explicitly unresolved.
            old_base = attrs.get('impedance_base_mva')
            for key in ('r_pu', 'x_pu'):
                if attrs.get(key) is not None:
                    result[key] = attrs[key] * base / old_base if old_base else None
                    provenance[key] = {'method': 'MVA_base_change_same_unspecified_voltage_base', 'source_base_mva': old_base}
    if kind in TRANSFORMERS:
        hv, lv = attrs.get('hv_kv'), attrs.get('lv_kv')
        if hv and lv and hv < lv:
            issues.append('Transformer HV rating is below its LV rating')
        if attrs.get('unit_rating_mva') is not None:
            derived(result, provenance, 'rating_mva', attrs['unit_rating_mva'] * parallel,
                    'unit_nameplate_MVA_times_identical_parallel', parallel=parallel)
        # Supplied transformer per-unit impedance has an MVA base too. The
        # winding voltage bases are unchanged unless explicitly specified.
        for axis in ('r', 'x'):
            pu = axis + '_pu'
            if attrs.get(pu) is not None:
                old_base = attrs.get('impedance_base_mva')
                if old_base is None:
                    result[pu] = None
                    issues.append(f'{pu} has no source impedance_base_mva; conversion unresolved')
                else:
                    result[pu] = attrs[pu] * base / old_base
                    provenance[pu] = {'method': 'transformer_MVA_base_change', 'source_base_mva': old_base, 'base_mva': base}
        windings = attrs.get('windings')
        if windings and (not isinstance(windings, list) or len(windings) != 2):
            issues.append('Transformer requires a separate multi-winding model; scalar branch impedance not inferred')
            result['r_pu'] = result['x_pu'] = None
            if isinstance(windings, list):
                try:
                    result['voltage_levels_kv'] = sorted(set(result['voltage_levels_kv'] + [
                        number(w.get('voltage_kv', w.get('PowerTransformerEnd.ratedU')), 'winding voltage', positive=True)
                        for w in windings]))
                except (ValueError, AttributeError):
                    issues.append('Missing or malformed winding voltage')
            if len(result['voltage_levels_kv']) > 1:
                result['voltage_kv'] = None
            return
        if isinstance(windings, list) and len(windings) == 2:
            try:
                u = [number(w.get('voltage_kv', w.get('PowerTransformerEnd.ratedU')), 'winding voltage', positive=True) for w in windings]
                hv, lv = max(u), min(u)
                result['voltage_levels_kv'] = sorted(set(result['voltage_levels_kv'] + u))
                reference = attrs.get('impedance_voltage_kv', hv)
                if not any(math.isclose(reference, v, rel_tol=1e-6) for v in u):
                    raise ValueError('Transformer impedance voltage reference must match a stated winding rating')
                rating_values = [w.get('rating_mva', w.get('PowerTransformerEnd.ratedS')) for w in windings]
                if all(v is not None for v in rating_values):
                    rating = min(number(v, 'winding rating_mva', positive=True) for v in rating_values) * parallel
                    if result['rating_mva'] is not None and not math.isclose(result['rating_mva'], rating, rel_tol=1e-4):
                        issues.append('Transformer aggregate rating disagrees with limiting winding rating')
                    derived(result, provenance, 'rating_mva', rating, 'limiting_two_winding_MVA_times_parallel', parallel=parallel)
                for axis in ('r', 'x'):
                    try:
                        values = [number(w.get(axis + '_ohm', w.get('PowerTransformerEnd.' + axis)), f'winding {axis}_ohm') for w in windings]
                    except ValueError:
                        # Missing winding resistance does not erase known reactance.
                        issues.append(f'Incomplete winding {axis}_ohm; that equivalent is unresolved')
                        continue
                    # Refer each winding's impedance to its own rated voltage base before summing.
                    equivalent = sum(z * base / v ** 2 for z, v in zip(values, u)) / parallel
                    if result[axis + '_pu'] is not None and not math.isclose(result[axis + '_pu'], equivalent, rel_tol=1e-4, abs_tol=1e-8):
                        issues.append(f'Supplied {axis}_pu disagrees with winding impedances')
                    derived(result, provenance, axis + '_pu', equivalent, 'sum_two_winding_impedances_on_common_MVA_base', base_mva=base, voltage_bases_kv=u)
                    derived(result, provenance, axis + '_ohm', equivalent * reference ** 2 / base,
                            'equivalent_referred_to_stated_winding_side', voltage_kv=reference)
            except ValueError as error:
                issues.append(str(error))
        if hv and lv:
            result['voltage_levels_kv'] = sorted(set(result['voltage_levels_kv'] + [hv, lv]))
            result['nominal_turns_ratio'] = hv / lv
            provenance['nominal_turns_ratio'] = {'method': 'rated_HV_divided_by_rated_LV', 'not_an_off_nominal_pu_tap': True}
            result['impedance_voltage_kv'] = hv
        for terminal in attrs.get('terminals', []):
            result['voltage_levels_kv'] = sorted(set(result['voltage_levels_kv'] + [number(terminal['voltage_kv'], 'terminal voltage', positive=True)]))
        if attrs.get('impedance_voltage_kv') is not None:
            reference = attrs['impedance_voltage_kv']
            if hv and lv and not any(math.isclose(reference, v, rel_tol=1e-6) for v in (hv, lv)):
                issues.append('Transformer impedance voltage reference must match a stated winding rating')
                result['r_pu'] = result['x_pu'] = None
                result['voltage_kv'] = None
                return
            # Scalar supplied ohms refer to this stated winding side. Convert to
            # its corresponding per-unit base, never assume which side was meant.
            result['impedance_voltage_kv'] = reference
            for axis in ('r', 'x'):
                if attrs.get(axis + '_ohm') is not None:
                    computed = attrs[axis + '_ohm'] * base / reference ** 2
                    if result[axis + '_pu'] is not None and not math.isclose(result[axis + '_pu'], computed, rel_tol=1e-4, abs_tol=1e-8):
                        issues.append(f'Inconsistent transformer {axis}_ohm and {axis}_pu')
                    derived(result, provenance, axis + '_pu', computed, 'transformer_ohms_on_explicit_winding_voltage_base', voltage_kv=reference, base_mva=base)
        elif any(attrs.get(k) is not None for k in ('r_ohm', 'x_ohm')):
            issues.append('Transformer scalar ohms need an explicit impedance_voltage_kv winding reference')
        if attrs.get('vk_percent') is not None and attrs.get('vkr_percent') is not None and result['rating_mva'] and (not windings or len(windings) == 2):
            z, r = attrs['vk_percent'] / 100, attrs['vkr_percent'] / 100
            if z < r:
                issues.append('Transformer vk_percent must be >= vkr_percent')
            else:
                factor = base / result['rating_mva']
                for key, computed in [('r_pu', r * factor), ('x_pu', math.sqrt(max(0., z*z-r*r)) * factor)]:
                    if result[key] is not None and not math.isclose(result[key], computed, rel_tol=1e-4, abs_tol=1e-8):
                        issues.append(f'Transformer {key} disagrees with nameplate impedance')
                    derived(result, provenance, key, computed, 'transformer_nameplate_percent_to_system_base', base_mva=base)
        if result['impedance_voltage_kv']:
            for axis in ('r', 'x'):
                if result[axis + '_pu'] is not None:
                    derived(result, provenance, axis + '_ohm', result[axis + '_pu'] * result['impedance_voltage_kv'] ** 2 / base,
                            'transformer_per_unit_to_stated_winding_ohms', voltage_kv=result['impedance_voltage_kv'], base_mva=base)
        # Scalar voltage is not meaningful across distinct transformer windings.
        if len(result['voltage_levels_kv']) > 1:
            result['voltage_kv'] = None
    if voltage and result['max_i_ka'] is not None and kind in LINES:
        rating = math.sqrt(3) * voltage * result['max_i_ka'] * parallel * attrs.get('derating_factor', 1.)
        derived(result, provenance, 'rating_mva', rating, 'balanced_three_phase_sqrt3_Vll_kV_I_kA', parallel=parallel,
                derating_factor=attrs.get('derating_factor', 1.))
    factor = attrs.get('power_factor', assumptions.get('power_factor'))
    if result['rating_mva'] is not None and factor is not None:
        derived(result, provenance, 'rating_mw', result['rating_mva'] * factor, 'MVA_times_explicit_power_factor', power_factor=factor)
    if result.get('x_pu') == 0 and kind in LINES | TRANSFORMERS:
        issues.append('Zero impedance requires topology contraction before a DC solve')


def required_fields(kind, result):
    if kind in LINES:
        return ['voltage_kv', 'x_pu', 'rating_mw', 'tap']
    if kind in TRANSFORMERS:
        return ['voltage_levels_kv', 'x_pu', 'rating_mw', 'tap']
    if kind in GENERATORS:
        return ['voltage_kv', 'generation_capacity_mw', 'generation_mw']
    if kind in SUPPORTS:
        return ['voltage_levels_kv']
    return ['voltage_kv', 'load_mw']


def import_json(engine, graph, mapping):
    if not isinstance(graph, dict):
        raise ValueError('Graph JSON must be an object')
    node_key = mapping.get('nodes_key', 'buses' if 'buses' in graph else 'nodes')
    edge_key = mapping.get('edges_key', 'branches' if 'branches' in graph else 'edges' if 'edges' in graph else 'links')
    nodes, edges = graph.get(node_key), graph.get(edge_key)
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError('Graph needs node and edge arrays (configure nodes_key/edges_key if renamed)')
    source_base = graph.get('base_mva')
    if source_base is not None:
        source_base = number(source_base, 'input base_mva', positive=True)
    for node in nodes:
        engine.add_entity(node, mapping=mapping.get('node_fields'), default_kind='bus', input_base=source_base)
    if engine.layout == 'incidence':
        for edge in edges:
            data = properties(edge)
            fields = mapping.get('edge_fields', {})
            engine.add_link(mapped(data, fields, 'from', ['from_node', 'from', 'source']),
                            mapped(data, fields, 'to', ['to_node', 'to', 'target']),
                            data.get('relation', 'electrical_connection'), data.get('evidence', 'input_declared_connectivity'), data.get('circuit_id'))
    else:
        engine.add_branches(edges, mapping.get('edge_fields'), source_base)
    for edge in graph.get('relationships', []):
        engine.add_link(edge.get('from_node', edge.get('source')), edge.get('to_node', edge.get('target')),
                        edge.get('relation', 'contains'), edge.get('evidence', 'input_relationship'))
    if 'generators' in graph:
        engine.add_generators(graph['generators'])
    engine.resolve_links()
    return node_key, edge_key


def import_sqlite(engine, path, mapping, source=None):
    with closing(sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        table = mapping.get('nodes_table', 'nodes')
        columns = [r[1] for r in db.execute(f'PRAGMA table_info({quoted(table)})')]
        if not columns:
            raise ValueError(f'Input has no {table} table')
        # Do not load multi-GB geometry blobs for electrical-property arithmetic.
        columns = [c for c in columns if not c.endswith('_wkb')]
        where, params = '', []
        if source:
            source_field = mapping.get('node_fields', {}).get('layer', 'source')
            where, params = f' WHERE {quoted(source_field)}=?', [source]
        cursor = db.execute(f'SELECT {",".join(map(quoted, columns))} FROM {quoted(table)}{where}', params)
        while rows := cursor.fetchmany(5000):
            for row in rows:
                engine.add_entity(dict(row), mapping=mapping.get('node_fields'), default_kind='junction')
            engine.db.commit()
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        for key, default in [('edges_table', 'edges'), ('relationships_table', 'relationships')]:
            table = mapping.get(key, default)
            if table not in tables:
                if key == 'edges_table':
                    raise ValueError(f'Input has no {table} table')
                continue
            columns = {r[1] for r in db.execute(f'PRAGMA table_info({quoted(table)})')}
            where, params = (' WHERE source=?', [source]) if source and 'source' in columns else ('', [])
            cursor = db.execute(f'SELECT * FROM {quoted(table)}{where}', params)
            while rows := cursor.fetchmany(10000):
                for row in rows:
                    data = dict(row)
                    fields = mapping.get('edge_fields', {})
                    engine.add_link(mapped(data, fields, 'from', ['from_node', 'from', 'source']),
                                    mapped(data, fields, 'to', ['to_node', 'to', 'target']),
                                    data.get('relation', 'electrical_connection' if key == 'edges_table' else 'contains'),
                                    data.get('evidence', 'input_declared_connectivity'), data.get('circuit_id'))
                engine.db.commit()
        engine.resolve_links(allow_filtered=source is not None)


def export_json(engine, graph, node_key, edge_key, mapping):
    for entity_type, records, fields in [('node', graph[node_key], mapping.get('node_fields', {})),
                                         ('edge', graph[edge_key], mapping.get('edge_fields', {}))]:
        for n, record in enumerate(records):
            raw = mapped(properties(record), fields, 'id', ['node_id', 'id'], f'edge:{n}')
            row = engine.db.execute('SELECT electrical FROM entities WHERE entity_type=? AND id=?', (entity_type, identifier(raw))).fetchone()
            if row:
                record['electrical'] = json.loads(row[0])
    graph['electrical_enrichment'] = engine.report
    return graph


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, required=True, help='New .json or .sqlite output; input is never modified')
    parser.add_argument('--layout', choices=['auto', 'node-link', 'incidence'], default='auto')
    parser.add_argument('--mapping', type=Path, help='JSON field/table mapping for another graph schema')
    parser.add_argument('--assumptions', type=Path, help='Explicit load weights, power factor, line types and dispatch assumptions')
    parser.add_argument('--base-mva', type=float, default=100.)
    parser.add_argument('--voltage-fallback', choices=['neighbour-average', 'none'], default='neighbour-average',
                        help='Fill unresolved single voltages using neighbour averages (default), or leave them unresolved')
    parser.add_argument('--source', help='SQLite source/layer filter (exact match)')
    parser.add_argument('--strict', action='store_true', help='Write report, then exit 2 if any required property or consistency check is unresolved')
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.resolve() == args.input.resolve():
        parser.error('Output must be a new path, different from the input')
    if not args.input.is_file():
        parser.error('Input file not found')
    is_sqlite = args.input.suffix.lower() in ('.sqlite', '.sqlite3', '.db')
    output_sqlite = args.output.suffix.lower() in ('.sqlite', '.sqlite3', '.db')
    if is_sqlite and not output_sqlite:
        parser.error('SQLite graphs require a SQLite sidecar output (bounded-memory processing)')
    if args.source and not is_sqlite:
        parser.error('--source applies to SQLite inputs')
    mapping = validate_mapping(json.loads(args.mapping.read_text()) if args.mapping else {})
    assumptions = json.loads(args.assumptions.read_text()) if args.assumptions else {}
    graph = None if is_sqlite else json.loads(args.input.read_text())
    layout = args.layout
    if layout == 'auto':
        layout = 'incidence' if is_sqlite or graph.get('representation') == 'incidence' else 'node-link'
    if is_sqlite and layout != 'incidence':
        parser.error('SQLite adapter expects equipment incidence; use JSON for node-link graphs')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='electrical-enrichment-', dir=args.output.parent) as temp:
        staged = Path(temp) / 'enriched.sqlite'
        engine = Enricher(staged, args.base_mva, assumptions, layout, args.voltage_fallback)
        try:
            print('Reading graph…', file=sys.stderr, flush=True)
            if is_sqlite:
                import_sqlite(engine, args.input, mapping, args.source)
            else:
                node_key, edge_key = import_json(engine, graph, mapping)
            if not engine.counts['nodes']:
                raise ValueError('Selected graph contains no nodes')
            print('Resolving voltage regions and deriving electrical properties…', file=sys.stderr, flush=True)
            engine.propagate()
            report = engine.derive()
            engine.db.execute('INSERT INTO metadata VALUES(?,?)', ('input', encode({'path': str(args.input.resolve()), 'source_filter': args.source})))
            engine.db.commit()
            if not output_sqlite:
                payload = export_json(engine, graph, node_key, edge_key, mapping)
                staged_json = Path(temp) / 'enriched.json'
                staged_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
        finally:
            engine.db.close()
        (staged if output_sqlite else staged_json).replace(args.output)
    print(json.dumps(report, indent=2))
    return 2 if args.strict and (report['missing_fields'] or report['counts'].get('records_with_issues')) else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, TypeError, sqlite3.Error) as error:
        print(f'Electrical enrichment failed: {error}', file=sys.stderr)
        sys.exit(1)
