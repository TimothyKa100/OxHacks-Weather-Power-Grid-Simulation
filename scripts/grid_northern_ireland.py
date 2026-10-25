"""Download a public NI OSM extract and preserve mapped power assets/topology."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.asset_rules import classify_osm
from failure_model.common import now, write_json

BASE = ROOT / 'data/grid'
RAW = BASE / 'raw/northern_ireland'
OUT = BASE / 'normalized'
URL = 'https://download.openstreetmap.fr/extracts/europe/united_kingdom/northern_ireland-latest.osm.pbf'
FALLBACK = 'https://download.geofabrik.de/europe/ireland-and-northern-ireland-latest.osm.pbf'


def main():
    import requests
    import osmium
    import pandas as pd
    import shapely
    from shapely.geometry import Point, LineString, Polygon
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / 'ireland-and-northern-ireland.osm.pbf'
    source_url = FALLBACK
    if not path.exists():
        response = requests.get(source_url, stream=True, timeout=(15, 120))
        response.raise_for_status()
        with path.with_suffix('.part').open('wb') as output:
            for chunk in response.iter_content(1024 * 1024):
                output.write(chunk)
        path.with_suffix('.part').replace(path)
    check_url = source_url + '.md5'
    checksum_path=RAW/'source.md5'
    if checksum_path.exists():
        check_text=checksum_path.read_text()
    else:
        check=requests.get(check_url,timeout=30);check.raise_for_status();check_text=check.text
    expected = check_text.split()[0]
    actual = hashlib.md5(path.read_bytes()).hexdigest()
    if expected != actual:
        raise ValueError('NI extract checksum mismatch; no assets imported')
    checksum_path.write_text(check_text)
    records, ways, exclusions = [], [], Counter()
    boundaries = []
    coordinates, tagged = {}, {}

    def record(ident, kind, tags, geometry, edited, method):
        point = geometry.representative_point() if geometry is not None and not geometry.is_empty else None
        watts = tags.get('plant:output:electricity', tags.get('generator:output:electricity', ''))
        import re
        m = re.fullmatch(r'([\d.]+)\s*(kW|MW|GW)', watts, re.I)
        capacity = float(m[1]) * {'kw': .001, 'mw': 1., 'gw': 1000.}[m[2].lower()] if m else None
        voltage = []
        for value in tags.get('voltage', '').split(';'):
            try: voltage.append(float(value) / 1000)
            except ValueError: pass
        return {'node_id': 'OSM:' + ident.replace('/', ':'), 'node_type': kind, 'source': 'OSM_NI',
                'source_asset_id': ident, 'operator': tags.get('operator'), 'name': tags.get('name'),
                'longitude': point.x if point else None, 'latitude': point.y if point else None,
                'geometry_wgs84_wkb': geometry.wkb if geometry is not None else None,
                'location_method': method, 'voltage_kv': max(voltage) if voltage else None,
                'generation_capacity_mw': capacity, 'lifecycle_status': 'existing_as_mapped',
                'topology_layer': 'OSM_NI', 'electrical_parameters_status': 'not_published',
                'properties_json': json.dumps({**tags, 'osm_last_edit': edited, 'coverage_region': 'Northern Ireland'}),
                'source_ref': 'data/grid/raw/northern_ireland/ireland-and-northern-ireland.osm.pbf'}

    class Handler(osmium.SimpleHandler):
        def area(self, item):
            tags = dict(item.tags)
            if tags.get('ISO3166-2') == 'GB-NIR' or (tags.get('admin_level') == '4' and tags.get('name', '').casefold() == 'northern ireland'):
                boundaries.append(shapely.from_wkb(bytes.fromhex(osmium.geom.WKBFactory().create_multipolygon(item))))
            elif 'power' in tags and not item.from_way():
                kind, _ = classify_osm(tags)
                if kind is not None:
                    geom = shapely.from_wkb(bytes.fromhex(osmium.geom.WKBFactory().create_multipolygon(item)))
                    records.append(record('relation/' + str(item.orig_id()), kind, tags, geom, str(item.timestamp), 'mapped_area_representative_point'))

        def node(self, item):
            tags = dict(item.tags)
            if 'power' not in tags: return
            kind, reason = classify_osm(tags)
            if kind is None:
                exclusions[reason] += 1
                return
            geom = Point(item.location.lon, item.location.lat)
            tagged[item.id] = kind
            records.append(record('node/' + str(item.id), kind, tags, geom, str(item.timestamp), 'mapped_point'))

        def way(self, item):
            tags = dict(item.tags)
            if 'power' not in tags: return
            kind, reason = classify_osm(tags)
            if kind is None:
                exclusions[reason] += 1
                return
            refs = [n.ref for n in item.nodes]
            try: points = [(n.location.lon, n.location.lat) for n in item.nodes]
            except osmium.InvalidLocationError:
                exclusions['unresolved_way_geometry'] += 1
                return
            if len(points) < 2: return
            for ref, point in zip(refs, points): coordinates[ref] = point
            if kind == 'power_line':
                ways.append((item.id, refs, points, tags, str(item.timestamp)))
            else:
                geom = Polygon(points) if len(points) >= 4 and refs[0] == refs[-1] else LineString(points)
                records.append(record('way/' + str(item.id), kind, tags, shapely.make_valid(geom), str(item.timestamp), 'mapped_geometry_representative_point'))

        def relation(self, item):
            tags = dict(item.tags)
            if 'power' not in tags: return
            kind, reason = classify_osm(tags)
            if kind is None:
                exclusions[reason] += 1
                return
            # Preserve unlocated relations rather than guessing a plant/line coordinate.
            records.append(record('relation/' + str(item.id), kind, tags, None, str(item.timestamp), 'unlocated_mapped_relation'))

    boundary_path=RAW/'northern_ireland_boundary.geojson'
    if boundary_path.exists() and boundary_path.stat().st_mtime>=path.stat().st_mtime:
        boundaries.append(shapely.from_geojson(boundary_path.read_text()))
    print('Reading NI power objects', flush=True)
    Handler().apply_file(str(path), locations=True, idx='flex_mem', filters=[osmium.filter.KeyFilter('power','ISO3166-2')])
    if not boundaries:
        raise ValueError('The NI administrative boundary could not be resolved; refusing to import Republic of Ireland assets')
    boundary = shapely.union_all(boundaries)
    shapely.prepare(boundary)
    (RAW / 'northern_ireland_boundary.geojson').write_text(shapely.to_geojson(boundary))
    records=[r for r in records if r['longitude'] is not None]
    inside=shapely.covers(boundary,shapely.points([r['longitude'] for r in records],[r['latitude'] for r in records]))
    records=[r for r,keep in zip(records,inside) if keep]
    ways = [w for w in ways if boundary.intersects(LineString(w[2]))]
    use = Counter(ref for _, refs, _, _, _ in ways for ref in set(refs))
    junctions, edges = {}, []
    from pyproj import Geod
    geod = Geod(ellps='WGS84')
    for ident, refs, points, tags, edited in ways:
        cuts = [0] + [i for i, ref in enumerate(refs[1:-1], 1) if use[ref] > 1 or tagged.get(ref) in ('switch', 'breaker', 'transformer', 'junction', 'terminal')] + [len(refs) - 1]
        signature = tags.get('voltage', 'unknown')
        for number, (start, end) in enumerate(zip(cuts, cuts[1:])):
            geom = LineString(points[start:end + 1])
            row = record('way/' + str(ident), 'power_line', tags, geom, edited, 'mapped_route_midpoint')
            row['node_id'] += ':section:' + str(number)
            mid = geom.interpolate(.5, normalized=True)
            if not boundary.covers(mid):
                clipped = geom.intersection(boundary)
                if clipped.is_empty: continue
                mid = clipped.representative_point()
            row.update(longitude=mid.x, latitude=mid.y, length_km=abs(geod.geometry_length(geom)) / 1000)
            records.append(row)
            for ref in [refs[start], refs[end]]:
                # Supports do not electrically short all circuits sharing a pole/tower.
                key = f'OSM:NI:route_port:{ident}:{ref}' if tagged.get(ref) in ('pole', 'tower', 'gantry') else f'OSM:NI:junction:{ref}:voltage:{signature}'
                if key not in junctions:
                    junctions[key] = {**record('node/' + str(ref), 'junction', {}, Point(coordinates[ref]), edited, 'mapped_route_vertex'), 'node_id': key}
                edges.append({'from_node': row['node_id'], 'to_node': key, 'source': 'OSM_NI',
                              'relation': 'line_terminal', 'evidence': 'mapped_shared_vertex_voltage_compatible_not_operator_verified'})
    records += list(junctions.values())
    pd.DataFrame(records).to_parquet(OUT / 'NI_power_nodes.parquet', index=False, compression='zstd')
    pd.DataFrame(edges, columns=['from_node', 'to_node', 'source', 'relation', 'evidence']).to_parquet(OUT / 'NI_power_edges.parquet', index=False)
    report = {'retrieved_utc': now(), 'source': source_url, 'published_md5': actual,
              'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size,
              'license': 'ODbL-1.0', 'attribution': '© OpenStreetMap contributors; extract by Geofabrik',
              'nodes': len(records), 'edges': len(edges), 'excluded': dict(exclusions),
              'scope': 'Northern Ireland mapped electricity components; not operator-certified connectivity'}
    write_json(BASE / 'reports/northern_ireland.json', report)
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__': main()
