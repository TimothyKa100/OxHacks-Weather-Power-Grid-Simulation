"""Build an audited electricity-only working database, keeping original sources intact."""
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.asset_rules import CATEGORIES, TYPE_CATEGORY, classify_osm, is_storage
from failure_model.common import now, write_json

BASE = ROOT / 'data/grid'


def clean_database(source, destination, ni_nodes=None, ni_edges=None):
    """Make a complete derivative, remove unrelated records and prune dangling references."""
    source, destination = Path(source), Path(destination)
    temporary = destination.with_suffix('.build.sqlite')
    if temporary.exists(): temporary.unlink()
    with sqlite3.connect(f'{source.resolve().as_uri()}?mode=ro', uri=True) as original, sqlite3.connect(temporary) as db:
        original.backup(db)
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('CREATE TABLE excluded_nodes(original_rowid INTEGER,node_id TEXT PRIMARY KEY,node_type TEXT,source TEXT,name TEXT,reason TEXT,properties_json TEXT)')
        db.execute('ALTER TABLE nodes ADD COLUMN original_node_type TEXT')
        db.execute('ALTER TABLE nodes ADD COLUMN asset_category TEXT')
        db.execute('ALTER TABLE nodes ADD COLUMN node_scope TEXT')
        db.execute('ALTER TABLE nodes ADD COLUMN storage_capacity_mwh REAL')
        db.execute('UPDATE nodes SET original_node_type=node_type')
        original_count = db.execute('SELECT COUNT(*) FROM nodes').fetchone()[0]
        decisions, exclusions = [], Counter()
        all_updates, all_rejected = [], []
        # Only these source groups require JSON examination; ordinary grid topology
        # and published operator substations must not be removed because of a name.
        cursor = db.execute("SELECT rowid,node_id,node_type,source,name,properties_json FROM nodes WHERE node_type IN ('other_power_equipment','generation','distributed_generator','switch')")
        while True:
            rows = cursor.fetchmany(20000)
            if not rows: break
            updates, rejected = [], []
            for rowid, ident, before, source_name, name, payload in rows:
                props = json.loads(payload or '{}')
                after, reason = before, 'published_operator_or_electrical_model_component'
                if source_name == 'OSM':
                    after, reason = classify_osm(props)
                elif is_storage(props):
                    after, reason = 'storage', 'published_storage_technology'
                if after not in TYPE_CATEGORY:
                    rejected.append((rowid, ident, before, source_name, name, reason, payload))
                    exclusions[reason] += 1
                    continue
                if after != before:
                    updates.append((after, ident))
                    decisions.append({'node_id': ident, 'before': before, 'after': after, 'reason': reason})
            all_rejected.extend(rejected)
            all_updates.extend(updates)
        db.executemany('INSERT INTO excluded_nodes VALUES (?,?,?,?,?,?,?)', all_rejected)
        db.executemany('UPDATE nodes SET node_type=? WHERE node_id=?', all_updates)
        unknown = db.execute('SELECT rowid,node_id,node_type,source,name,properties_json FROM nodes WHERE node_type NOT IN (' + ','.join('?' for _ in TYPE_CATEGORY) + ') AND node_id NOT IN (SELECT node_id FROM excluded_nodes)', list(TYPE_CATEGORY)).fetchall()
        db.executemany('INSERT INTO excluded_nodes VALUES (?,?,?,?,?,?,?)',
                       [(rid, ident, kind, src, name, 'not_an_allowlisted_grid_component', props) for rid, ident, kind, src, name, props in unknown])
        for table in ['edges', 'relationships', 'candidate_edges']:
            db.execute(f'DELETE FROM {table} WHERE from_node IN (SELECT node_id FROM excluded_nodes) OR to_node IN (SELECT node_id FROM excluded_nodes)')
        db.execute('DELETE FROM spatial_index WHERE id IN (SELECT original_rowid FROM excluded_nodes)')
        db.execute('DELETE FROM nodes WHERE node_id IN (SELECT node_id FROM excluded_nodes)')
        ni_added = 0
        if ni_nodes and Path(ni_nodes).exists():
            columns = [r[1] for r in db.execute('PRAGMA table_info(nodes)')]
            import shapely
            for batch in pq.ParquetFile(ni_nodes).iter_batches(batch_size=10000):
                d = batch.to_pandas()
                d['original_node_type'] = d.node_type
                d = d.reindex(columns=columns)
                bounds = shapely.bounds(shapely.from_wkb(d.geometry_wgs84_wkb.to_numpy()))
                for index, col in enumerate(['bbox_min_lon','bbox_min_lat','bbox_max_lon','bbox_max_lat']): d[col] = bounds[:, index]
                data = d.astype(object).where(d.notna(), None)
                before = db.total_changes
                db.executemany('INSERT OR IGNORE INTO nodes (' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')', data.itertuples(index=False,name=None))
                ni_added += db.total_changes - before
            if ni_edges and Path(ni_edges).exists():
                for batch in pq.ParquetFile(ni_edges).iter_batches():
                    db.executemany('INSERT INTO edges VALUES (?,?,?,?,?)', batch.to_pandas().itertuples(index=False,name=None))
            db.execute("INSERT INTO spatial_index SELECT rowid,bbox_min_lon,bbox_max_lon,bbox_min_lat,bbox_max_lat FROM nodes WHERE source='OSM_NI' AND bbox_min_lon IS NOT NULL AND rowid NOT IN (SELECT id FROM spatial_index)")
            # Cached NI exports may predate the current electricity-only rules.
            rejected = []
            for rowid, ident, kind, src, name, payload in db.execute("SELECT rowid,node_id,node_type,source,name,properties_json FROM nodes WHERE source='OSM_NI' AND node_type IN ('generation','distributed_generator','storage')"):
                after, reason = classify_osm(json.loads(payload or '{}'))
                if after is None:
                    rejected.append((rowid,ident,kind,src,name,reason,payload))
            db.executemany('INSERT INTO excluded_nodes VALUES (?,?,?,?,?,?,?)',rejected)
        # Prune again after NI import so rejected imported endpoints cannot survive.
        for table in ['edges', 'relationships', 'candidate_edges']:
            db.execute(f'DELETE FROM {table} WHERE from_node IN (SELECT node_id FROM excluded_nodes) OR to_node IN (SELECT node_id FROM excluded_nodes)')
        db.execute('DELETE FROM spatial_index WHERE id IN (SELECT n.rowid FROM nodes n JOIN excluded_nodes e ON e.node_id=n.node_id)')
        db.execute('DELETE FROM nodes WHERE node_id IN (SELECT node_id FROM excluded_nodes)')
        for kind, category in TYPE_CATEGORY.items():
            scope = 'topology' if kind in CATEGORIES['connection']['types'] else 'aggregate' if kind in ('feeder','circuit','equivalent_branch') else 'physical'
            db.execute('UPDATE nodes SET asset_category=?,node_scope=? WHERE node_type=?', (category,scope,kind))
        db.execute('CREATE VIRTUAL TABLE node_point_index USING rtree(id,min_lon,max_lon,min_lat,max_lat)')
        db.execute('INSERT INTO node_point_index SELECT rowid,longitude,longitude,latitude,latitude FROM nodes WHERE longitude BETWEEN -180 AND 180 AND latitude BETWEEN -90 AND 90')
        print('Building complete national point overview',flush=True)
        db.execute('''CREATE TABLE node_cells AS SELECT CAST((longitude+180)*20 AS INTEGER) AS cell_x,
            CAST((latitude+90)*20 AS INTEGER) AS cell_y,asset_category,source,COUNT(*) AS count,
            SUM(longitude) AS longitude_sum,SUM(latitude) AS latitude_sum,MIN(node_id) AS sample_id
            FROM nodes WHERE longitude BETWEEN -180 AND 180 AND latitude BETWEEN -90 AND 90
            GROUP BY cell_x,cell_y,asset_category,source''')
        db.execute('CREATE INDEX node_cells_xy ON node_cells(cell_x,cell_y)')
        db.execute('CREATE INDEX nodes_category_source ON nodes(asset_category,source)')
        db.execute('CREATE INDEX nodes_name ON nodes(name COLLATE NOCASE)')
        counts = dict(db.execute('SELECT node_type,count(*) FROM nodes GROUP BY node_type'))
        removed = db.execute('SELECT count(*) FROM excluded_nodes').fetchone()[0]
        total = db.execute('SELECT count(*) FROM nodes').fetchone()[0]
        located = db.execute('SELECT count(*) FROM node_point_index').fetchone()[0]
        report = {'generated_utc':now(),'source_database':str(source),'nodes_before':original_count,
                  'retained_nodes':total,'removed_nodes':removed,'northern_ireland_nodes_added':ni_added,
                  'located_nodes':located,'unlocated_nodes':total-located,'node_types':counts,
                  'reclassified_nodes':len(decisions),'exclusion_reasons':dict(db.execute('SELECT reason,count(*) FROM excluded_nodes GROUP BY reason')),
                  'categories':CATEGORIES,'scope':'UK mapped/operator power components; source representations overlap',
                  'weather_geometry':'node point only; no route averaging','flood_depth_available':False,
                  'outage_scope':'Available SSEN incident reports, not all UK outages in all history'}
        db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('cleaning',json.dumps(report)))
        db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('coverage',json.dumps('Great Britain operator/mapped assets plus Northern Ireland OSM' if ni_added else 'Great Britain; Northern Ireland extract unavailable')))
        db.commit()
        assert total == original_count - removed + ni_added
        for table in ['edges','relationships','candidate_edges']:
            missing = db.execute(f'SELECT count(*) FROM {table} e LEFT JOIN nodes a ON a.node_id=e.from_node LEFT JOIN nodes b ON b.node_id=e.to_node WHERE a.node_id IS NULL OR b.node_id IS NULL').fetchone()[0]
            if missing: raise ValueError(f'{table} has {missing} dangling references')
        pd.read_sql_query('SELECT * FROM excluded_nodes',db).to_parquet(BASE/'normalized/excluded_non_grid_nodes.parquet',index=False)
    pd.DataFrame(decisions).to_parquet(BASE/'normalized/grid_reclassification_audit.parquet',index=False)
    temporary.replace(destination)
    write_json(BASE/'reports/cleaning.json',report)
    return report


def main():
    report=clean_database(BASE/'british_grid.sqlite',BASE/'uk_power_grid.sqlite',
                          BASE/'normalized/NI_power_nodes.parquet',BASE/'normalized/NI_power_edges.parquet')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
