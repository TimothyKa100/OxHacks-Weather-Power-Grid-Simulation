"""Explicit electricity-grid taxonomy; a generic OSM power key is not sufficient."""
import re

OSM_TYPES = {
    'plant': 'generation', 'generator': 'distributed_generator', 'substation': 'substation',
    'transformer': 'transformer', 'line': 'power_line', 'minor_line': 'power_line',
    'cable': 'power_line', 'minor_cable': 'power_line', 'minor_underground_cable': 'power_line',
    'busbar': 'busbar', 'bay': 'busbar', 'switch': 'switch', 'disconnect_switch': 'disconnector',
    'switchgear': 'switchgear', 'circuit_breaker': 'breaker', 'breaker': 'breaker',
    'fuse': 'fuse', 'cable_distribution_cabinet': 'distribution_cabinet',
    'converter': 'converter', 'compensator': 'compensator', 'capacitor_bank': 'compensator',
    'inverter': 'inverter', 'terminal': 'terminal', 'termination': 'terminal',
    'connection': 'junction', 'transition': 'terminal', 'pole': 'pole', 'tower': 'tower',
    'portal': 'gantry', 'insulator': 'insulator', 'cross_arm': 'cross_arm', 'circuit': 'circuit',
    'storage': 'storage',
}
CATEGORIES = {
    'generation': {'label': 'Plants & distributed generation', 'types': ['generation', 'distributed_generator', 'generator_machine', 'inverter']},
    'storage': {'label': 'Energy storage', 'types': ['storage']},
    'substation': {'label': 'Substations', 'types': ['substation', 'substation_equipment_record']},
    'transformer': {'label': 'Transformers', 'types': ['transformer']},
    'link': {'label': 'Lines & circuit equipment', 'types': ['power_line', 'circuit', 'feeder', 'equivalent_branch']},
    'switchgear': {'label': 'Breakers & switchgear', 'types': ['breaker', 'switch', 'disconnector', 'switchgear', 'fuse', 'distribution_cabinet', 'compensator', 'converter', 'busbar']},
    'support': {'label': 'Poles, towers & supports', 'types': ['pole', 'tower', 'gantry', 'insulator', 'cross_arm']},
    'connection': {'label': 'Electrical junctions & terminals', 'types': ['junction', 'terminal', 'load_connection', 'supply_boundary']},
}
TYPE_CATEGORY = {kind: category for category, definition in CATEGORIES.items() for kind in definition['types']}
LABELS = {'generation': 'Power plant', 'distributed_generator': 'Distributed generator', 'storage': 'Energy storage',
          'power_line': 'Power line', 'substation': 'Substation', 'substation_equipment_record': 'Substation equipment',
          'breaker': 'Circuit breaker', 'gantry': 'Power gantry', 'load_connection': 'Electrical load connection'}
LIFECYCLE = ('construction', 'proposed', 'planned', 'disused', 'abandoned', 'demolished', 'dismantled', 'removed')


def is_storage(properties):
    # Technical fields only: a shop/street/site name containing "Battery" is not evidence.
    keys = ['plant:source', 'generator:source', 'plant:method', 'generator:method', 'technology',
            'Technology', 'Technology Type', 'Type', 'Primary Fuel', 'energy_source', 'energy_conversion_technology_1']
    return any(re.search(r'batter|pumped|electrochemical|compressed.air|flywheel', str(properties.get(k, '')), re.I) for k in keys)


def classify_osm(tags):
    """Return (canonical type or None, reason), independent of the site's name."""
    if any(tags.get(k) not in (None, '', 'no', 'false', '0') for k in LIFECYCLE):
        return None, 'non_operational_or_uncertain'
    tokens = [t.strip().casefold() for t in str(tags.get('power', '')).split(';')]
    if any(t in LIFECYCLE for t in tokens):
        return None, 'non_operational_or_uncertain'
    recognized = [OSM_TYPES[t] for t in tokens if t in OSM_TYPES]
    if not recognized:
        return None, 'not_an_allowlisted_electricity_grid_component'
    if any(kind in recognized for kind in ('generation', 'distributed_generator', 'storage')):
        outputs = [str(tags.get(f'{prefix}:output:electricity', '')).strip().casefold()
                   for prefix in ('generator', 'plant')]
        zero_outputs = ('no', 'false', '0', '0 w', '0 kw', '0 mw')
        electric = any(value and value not in zero_outputs for value in outputs)
        heating_device = (tags.get('generator:type') in ('solar_thermal_collector', 'heat_pump', 'boiler')
                          or (tags.get('generator:source') == 'solar' and tags.get('generator:method') in ('thermal', 'solar_thermal')))
        if not electric and (any(value in zero_outputs for value in outputs) or heating_device):
            return None, 'non_electric_heating_equipment'
        heat_output = any(str(tags.get(f'{prefix}:output:{output}', '')).strip().casefold() not in ('', *zero_outputs)
                          for prefix in ('generator', 'plant') for output in ('heat', 'hot_water', 'steam'))
        electric_method = tags.get('generator:method') == 'photovoltaic' or tags.get('generator:type') == 'solar_photovoltaic_panel'
        electrical_rating = any(tags.get(key) not in (None, '', '0', 'no') for key in ('frequency', 'voltage'))
        if heat_output and not (electric or electric_method or electrical_rating):
            return None, 'heating_without_electricity_evidence'
    if is_storage(tags) and any(t in recognized for t in ['generation', 'distributed_generator', 'storage']):
        return 'storage', 'published_storage_technology'
    if 'switch' in recognized and tags.get('switch') in ('circuit_breaker', 'breaker'):
        return 'breaker', 'published_circuit_breaker_tag'
    if 'switch' in recognized and tags.get('switch') in ('disconnector', 'disconnect', 'isolator'):
        return 'disconnector', 'published_disconnector_tag'
    priority = ['storage', 'generation', 'substation', 'transformer', 'distributed_generator', 'breaker',
                'switchgear', 'disconnector', 'switch', 'power_line', 'busbar', 'fuse', 'distribution_cabinet',
                'converter', 'compensator', 'inverter', 'terminal', 'junction', 'circuit', 'tower', 'gantry', 'pole', 'insulator', 'cross_arm']
    return min(recognized, key=priority.index), 'explicit_electricity_component_tag'


def display_name(node_type, name):
    label = LABELS.get(node_type, node_type.replace('_', ' ').capitalize())
    return f'{label} · {name}' if name else label


def minimum_zoom(kind, voltage=None, capacity=None):
    """Persistent large/rare infrastructure, progressively revealed local equipment."""
    voltage = voltage or 0
    if kind == 'generation': return 4
    if kind in ('distributed_generator', 'generator_machine', 'inverter'):
        return 4 if (capacity or 0) >= 50 else 10
    if kind == 'substation': return 4 if voltage >= 132 else 6 if voltage >= 33 else 8
    if kind in ('storage', 'converter', 'compensator', 'supply_boundary'): return 6
    if kind in ('power_line', 'circuit', 'feeder', 'equivalent_branch'):
        return 4 if voltage >= 132 else 8 if voltage >= 33 else 11
    if kind == 'tower': return 13
    if kind in ('pole', 'junction', 'terminal', 'load_connection', 'insulator', 'cross_arm'): return 15
    if kind in ('transformer', 'substation_equipment_record'): return 10
    return 13


def tower_height(properties, voltage=None):
    """(estimate, lower, upper, basis); estimates are not surveyed clearance."""
    measured = str(properties.get('height', '')).strip()
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(m|metres|meters)?', measured, re.I)
    if match and 0 < float(match[1]) < 200:
        h = float(match[1]); return h, h, h, 'source_tag_height_unverified'
    design = ' '.join(str(properties.get(k, '')) for k in ('design', 'design:ref', 'tower:design', 'structure', 'ref', 'tower:type', 'model')).lower()
    if re.search(r't[ -]?pylon', design): return 35., 35., 35., 'assumed_T_pylon_35m'
    if re.search(r'\bl4\b', design): return 26., 26., 26., 'assumed_L4_26m'
    if re.search(r'\bl[26]\b', design) or (voltage or 0) >= 275:
        return 46., 42., 50., 'assumed_L2_L6_range_midpoint'
    if voltage == 132: return 26., 26., 26., 'assumed_132kV_L4_26m'
    return None, None, None, 'unknown_design_and_voltage'
