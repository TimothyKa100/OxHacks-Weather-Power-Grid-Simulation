"""Apply UK land scope, Atlas enrichment, display hierarchy and tower heights.

This transforms only the working catalogue. Original operator/model downloads
remain reproducible evidence; removed rows are exported to an exclusion audit.
Run after clean_power_grid.py and prepare_grid_conditions.py.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pandas as pd
import shapely
from shapely.geometry import shape
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.asset_rules import TYPE_CATEGORY, minimum_zoom, tower_height
from failure_model.common import now, write_json

BASE = ROOT / 'data/grid'


def land_boundary(path=None):
    data = json.loads((path or BASE / 'raw/uk_land_boundary.geojson').read_text())
    land = shapely.union_all([shape(f['geometry']) for f in data['features']])
    if not land.is_valid: land = shapely.make_valid(land)
    shapely.prepare(land)
    return land


def on_land(land, longitude, latitude):
    x, y = np.asarray(longitude, dtype=float), np.asarray(latitude, dtype=float)
    return np.isfinite(x) & np.isfinite(y) & shapely.intersects_xy(land, x, y)


def import_atlas(db, land):
    folder = BASE / 'raw/uk_grid_atlas'
    manifest = json.loads((folder / 'manifest.json').read_text())
    columns = [r[1] for r in db.execute('PRAGMA table_info(nodes)')]
    counts = Counter()
    for country in ['gb', 'ie']:
        for bundle in ['stations', 'transmission']:
            path = folder / f'src/data/{country}/{bundle}.json'
            for index, f in enumerate(json.loads(path.read_text())['features']):
                g = shape(f['geometry']); props = f['properties']; point = g.representative_point()
                if not on_land(land, [point.x], [point.y])[0]: counts['excluded_offshore_or_non_UK'] += 1; continue
                osm_id = props.get('id')
                existing = None
                if osm_id:
                    tokens = osm_id.split('/')
                    if len(tokens) == 2:
                        existing = db.execute('SELECT node_id,properties_json FROM nodes WHERE node_id=?', ('OSM:' + ':'.join(tokens),)).fetchone()
                if existing:
                    original = json.loads(existing[1] or '{}'); original['uk_grid_atlas'] = props
                    db.execute('UPDATE nodes SET properties_json=? WHERE node_id=?', (json.dumps(original), existing[0]))
                    counts['existing_OSM_records_enriched'] += 1; continue
                key = osm_id or hashlib.sha256(g.wkb + json.dumps(props, sort_keys=True).encode()).hexdigest()[:24]
                ident = f'UK_GRID_ATLAS:{country}:{bundle}:{key}'
                kind = 'power_line' if bundle == 'transmission' else 'storage' if props.get('fuel') == 'battery' else 'generation'
                if bundle == 'transmission':
                    g = shapely.intersection(g, land)
                    if g.is_empty: continue
                row = dict(node_id=ident, node_type=kind, original_node_type=kind, source='UK_GRID_ATLAS',
                           source_asset_id=osm_id, name=props.get('name'), operator=props.get('operator'),
                           longitude=point.x, latitude=point.y, geometry_wgs84_wkb=g.wkb,
                           location_method='atlas_OSM_geometry_representative_point',
                           voltage_kv=props.get('v'), generation_capacity_mw=props.get('capacity'),
                           asset_category=TYPE_CATEGORY[kind], node_scope='physical',
                           topology_layer='UK_GRID_ATLAS', lifecycle_status='existing_as_published',
                           electrical_parameters_status='mapped_not_operator_certified', properties_json=json.dumps(props),
                           source_ref=f'https://github.com/jacobwright32/uk-grid-atlas/blob/{manifest["commit"]}/src/data/{country}/{bundle}.json')
                for name, value in zip(['bbox_min_lon','bbox_min_lat','bbox_max_lon','bbox_max_lat'], g.bounds): row[name] = value
                db.execute('INSERT OR IGNORE INTO nodes ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')', [row.get(c) for c in columns])
                counts['atlas_records_added'] += int(db.execute('SELECT changes()').fetchone()[0])
    db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('uk_grid_atlas', json.dumps(dict(commit=manifest['commit'], **counts))))
    return dict(counts)


def refine(db, land):
    db.execute('CREATE TEMP TABLE rejected(node_id TEXT PRIMARY KEY,reason TEXT)')
    audit = BASE / 'normalized/excluded_geographic_nodes.parquet'
    counts = Counter()
    with pq.ParquetWriter(audit, pa.schema([('node_id',pa.string()),('longitude',pa.float64()),('latitude',pa.float64()),('reason',pa.string())]), compression='zstd') as writer:
        cursor = db.execute('SELECT node_id,longitude,latitude FROM nodes')
        while rows := cursor.fetchmany(50000):
            frame = pd.DataFrame(rows, columns=['node_id','longitude','latitude'])
            keep = on_land(land, frame.longitude, frame.latitude)
            excluded = frame.loc[~keep].copy()
            excluded['reason'] = np.where(excluded.longitude.isna() | excluded.latitude.isna(), 'missing_coordinate', 'outside_UK_land')
            db.executemany('INSERT INTO rejected VALUES (?,?)', excluded[['node_id','reason']].itertuples(index=False, name=None))
            writer.write_table(pa.Table.from_pandas(excluded, schema=writer.schema, preserve_index=False))
            counts.update(excluded.reason.tolist())
    for table in ['edges','relationships','candidate_edges']:
        db.execute(f'DELETE FROM {table} WHERE from_node IN (SELECT node_id FROM rejected) OR to_node IN (SELECT node_id FROM rejected)')
    for table in ['node_weather_links','node_flood_history','seasonal_ratings','elevation_profiles']:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            names={r[1] for r in db.execute(f'PRAGMA table_info({table})')}
            if 'node_id' in names: db.execute(f'DELETE FROM {table} WHERE node_id IN (SELECT node_id FROM rejected)')
    db.execute('DELETE FROM spatial_index WHERE id IN (SELECT n.rowid FROM nodes n JOIN rejected r ON r.node_id=n.node_id)')
    db.execute('DELETE FROM nodes WHERE node_id IN (SELECT node_id FROM rejected)')
    # Clip the small minority of routes that cross the coast, leaving no offshore route geometry.
    cursor = db.execute("SELECT node_id,geometry_wgs84_wkb FROM nodes WHERE node_type='power_line' AND geometry_wgs84_wkb IS NOT NULL")
    while rows := cursor.fetchmany(10000):
        geometries = shapely.from_wkb([r[1] for r in rows])
        crossing = ~shapely.covers(land, geometries)
        updates=[]
        for i in np.flatnonzero(crossing):
            g = shapely.intersection(geometries[i], land)
            if not g.is_empty: updates.append((g.wkb, *g.bounds, rows[i][0]))
        db.executemany('UPDATE nodes SET geometry_wgs84_wkb=?,bbox_min_lon=?,bbox_min_lat=?,bbox_max_lon=?,bbox_max_lat=? WHERE node_id=?', updates)
        counts['coastal_routes_clipped'] += len(updates)
    return dict(counts)


def presentation(db):
    additions = {'min_zoom':'INTEGER', 'structure_height_m':'REAL', 'structure_height_min_m':'REAL',
                 'structure_height_max_m':'REAL', 'height_basis':'TEXT', 'asset_top_elevation_m':'REAL'}
    existing = {r[1] for r in db.execute('PRAGMA table_info(nodes)')}
    for column, kind in additions.items():
        if column not in existing: db.execute(f'ALTER TABLE nodes ADD COLUMN {column} {kind}')
    # A support can carry several voltage systems. Infer only a height class;
    # never add electrical connectivity between those systems.
    db.execute('''CREATE TEMP TABLE support_voltage AS SELECT r.from_node node_id,MAX(n.voltage_kv) voltage
        FROM relationships r JOIN nodes n ON n.node_id=r.to_node
        WHERE r.relation IN ('route_support_or_equipment_member','supports_or_contains_terminal')
        GROUP BY r.from_node''')
    db.execute('CREATE INDEX support_voltage_id ON support_voltage(node_id)')
    cursor=db.execute('SELECT n.node_id,n.node_type,n.voltage_kv,n.generation_capacity_mw,n.properties_json,n.terrain_elevation_m,s.voltage FROM nodes n LEFT JOIN support_voltage s ON s.node_id=n.node_id')
    heights=Counter()
    while rows := cursor.fetchmany(50000):
        updates=[]
        for ident, kind, voltage, capacity, payload, ground, carried in rows:
            height, lower, upper, basis = tower_height(json.loads(payload or '{}'), voltage or carried) if kind == 'tower' else (None,None,None,None)
            if kind=='tower': heights[basis]+=1
            updates.append((minimum_zoom(kind, voltage, capacity), height, lower, upper, basis,
                            ground+height if ground is not None and height is not None else None, ident))
        db.executemany('UPDATE nodes SET min_zoom=?,structure_height_m=?,structure_height_min_m=?,structure_height_max_m=?,height_basis=?,asset_top_elevation_m=? WHERE node_id=?', updates)
    print('Rebuilding geometry and point indexes',flush=True)
    db.execute('DELETE FROM spatial_index')
    db.execute('INSERT INTO spatial_index SELECT rowid,bbox_min_lon,bbox_max_lon,bbox_min_lat,bbox_max_lat FROM nodes WHERE bbox_min_lon IS NOT NULL')
    db.execute('DELETE FROM node_point_index')
    db.execute('INSERT INTO node_point_index SELECT rowid,longitude,longitude,latitude,latitude FROM nodes')
    print('Rebuilding national zoom summaries',flush=True)
    for table in ['node_cells','node_zoom_cells']: db.execute(f'DROP TABLE IF EXISTS {table}')
    db.execute('''CREATE TABLE node_zoom_cells AS SELECT CAST((longitude+180)*20 AS INTEGER) cell_x,
        CAST((latitude+90)*20 AS INTEGER) cell_y,asset_category,source,min_zoom,COUNT(*) count,
        SUM(longitude) longitude_sum,SUM(latitude) latitude_sum,MIN(node_id) sample_id
        FROM nodes GROUP BY cell_x,cell_y,asset_category,source,min_zoom''')
    db.execute('''CREATE TABLE node_cells AS SELECT cell_x,cell_y,asset_category,source,SUM(count) count,
        SUM(longitude_sum) longitude_sum,SUM(latitude_sum) latitude_sum,MIN(sample_id) sample_id
        FROM node_zoom_cells GROUP BY cell_x,cell_y,asset_category,source''')
    db.execute('CREATE INDEX node_cells_xy ON node_cells(cell_x,cell_y)')
    db.execute('CREATE INDEX node_zoom_cells_xy ON node_zoom_cells(cell_x,cell_y,min_zoom)')
    db.execute('CREATE INDEX IF NOT EXISTS nodes_zoom_type ON nodes(min_zoom,node_type)')
    return dict(heights)


def main():
    land=land_boundary()
    with sqlite3.connect(BASE/'uk_power_grid.sqlite') as db:
        if db.execute("SELECT 1 FROM metadata WHERE key='uk_refinement'").fetchone():
            print('Catalogue already refined. Rebuild the source-cleaned catalogue before reapplying geographic refinement; existing exclusion audits are preserved.',flush=True)
            return
        db.execute('PRAGMA cache_size=-100000')
        print('Applying geographic filter', flush=True); scope=refine(db,land)
        print('Importing UK Grid Atlas', flush=True); atlas=import_atlas(db,land)
        print('Building hierarchy and height estimates', flush=True); heights=presentation(db)
        # Apply the same geography rule to the active outage register.
        outages=pd.read_sql_query('SELECT * FROM outages',db)
        keep=on_land(land,outages.longitude,outages.latitude)
        outages.loc[~keep].to_parquet(BASE/'normalized/excluded_geographic_outages.parquet',index=False)
        db.executemany('DELETE FROM outages WHERE event_key=?',[(x,) for x in outages.loc[~keep,'event_key']])
        # A removed feeder must not remain a claimed asset association.
        db.execute("UPDATE outages SET location_asset_id=NULL WHERE location_asset_id NOT IN (SELECT node_id FROM nodes)")
        retained=db.execute('SELECT COUNT(*) FROM nodes').fetchone()[0]
        cleaning=json.loads(db.execute("SELECT value FROM metadata WHERE key='cleaning'").fetchone()[0])
        cleaning.update(retained_nodes=retained,located_nodes=retained,unlocated_nodes=0,
                        node_types=dict(db.execute('SELECT node_type,COUNT(*) FROM nodes GROUP BY node_type')))
        db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('cleaning',json.dumps(cleaning)))
        report=dict(generated_utc=now(), geographic_exclusions=scope,atlas=atlas,tower_heights=heights,
                    active_nodes=retained,outages_excluded=int((~keep).sum()),active_outages=int(keep.sum()),
                    boundary=json.loads((BASE/'raw/uk_boundary_metadata.json').read_text()),
                    height_note='Estimated structure top = unchanged ground terrain + source or assumed tower height; not conductor clearance. Unknown design/voltage stays null.')
        db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('uk_refinement',json.dumps(report)))
        db.commit()
        pd.read_sql_query('SELECT * FROM outages',db).to_parquet(BASE/'normalized/all_outage_reports.parquet',index=False)
    write_json(BASE/'reports/uk_refinement.json',report)
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__': main()
