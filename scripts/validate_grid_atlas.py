"""Validate the working electricity catalog and complete point/history overviews."""
from pathlib import Path
import json
import sqlite3
import sys

import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.asset_rules import TYPE_CATEGORY,classify_osm
from backend.app import app
from failure_model.common import now,write_json


def main():
    base=ROOT/'data/grid';path=base/'uk_power_grid.sqlite'
    with sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro',uri=True) as db:
        assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        nodes=db.execute('SELECT COUNT(*) FROM nodes').fetchone()[0]
        located=db.execute('SELECT COUNT(*) FROM node_point_index').fetchone()[0]
        assert not set(r[0] for r in db.execute('SELECT DISTINCT node_type FROM nodes'))-set(TYPE_CATEGORY)
        assert db.execute('SELECT COUNT(*) FROM nodes n JOIN excluded_nodes e USING(node_id)').fetchone()[0]==0
        for ident,payload in db.execute("SELECT node_id,properties_json FROM nodes WHERE source IN ('OSM','OSM_NI') AND node_type IN ('generation','distributed_generator','storage')"):
            assert classify_osm(json.loads(payload or '{}'))[0] is not None,ident
        assert db.execute('SELECT COUNT(*) FROM nodes n LEFT JOIN spatial_index s ON s.id=n.rowid WHERE n.bbox_min_lon IS NOT NULL AND s.id IS NULL').fetchone()[0]==0
        assert db.execute('SELECT SUM(count) FROM node_cells').fetchone()[0]==located
        assert db.execute('SELECT COUNT(*) FROM node_weather_links').fetchone()[0]==nodes
        assert db.execute('SELECT COUNT(*) FROM node_weather_links WHERE weather_key IS NOT NULL AND distance_km>35').fetchone()[0]==0
        assert pq.ParquetFile(base/'normalized/clean_grid_node_locations.parquet').metadata.num_rows==nodes
        assert pq.ParquetFile(base/'normalized/node_weather_links.parquet').metadata.num_rows==nodes
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='node_zoom_cells'").fetchone():
            from scripts.refine_uk_grid import land_boundary,on_land
            land=land_boundary()
            import pandas as pd
            for batch in pd.read_sql_query('SELECT longitude,latitude FROM nodes',db,chunksize=50000):
                assert on_land(land,batch.longitude,batch.latitude).all()
            assert db.execute('SELECT SUM(count) FROM node_zoom_cells').fetchone()[0]==nodes
            assert db.execute('SELECT COUNT(*) FROM nodes WHERE min_zoom IS NULL').fetchone()[0]==0
            assert db.execute('''SELECT COUNT(*) FROM nodes WHERE asset_top_elevation_m IS NOT NULL AND
                (terrain_elevation_m IS NULL OR structure_height_m IS NULL OR abs(asset_top_elevation_m-terrain_elevation_m-structure_height_m)>0.000001)''').fetchone()[0]==0
            assert db.execute("SELECT COUNT(*) FROM nodes WHERE node_type='pole' AND min_zoom<15").fetchone()[0]==0
        for table in ['edges','relationships','candidate_edges']:
            assert db.execute(f'SELECT COUNT(*) FROM {table} e LEFT JOIN nodes a ON a.node_id=e.from_node LEFT JOIN nodes b ON b.node_id=e.to_node WHERE a.node_id IS NULL OR b.node_id IS NULL').fetchone()[0]==0,table
        outages=db.execute('SELECT COUNT(*) FROM outages').fetchone()[0]
        mapped=db.execute('SELECT COUNT(*) FROM outages WHERE latitude IS NOT NULL').fetchone()[0]
        assert pq.ParquetFile(base/'normalized/all_outage_reports.parquet').metadata.num_rows==outages
        assert db.execute('SELECT SUM(training_eligible),SUM(asset_failure_verified),COUNT(failed_asset_id) FROM outages').fetchone()==(0,0,0)
        assert db.execute('SELECT COUNT(*) FROM outages WHERE flood_depth_m IS NOT NULL').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM outages WHERE location_asset_id IS NULL AND recorded_flood_outline_matches IS NOT NULL').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM node_flood_history h LEFT JOIN nodes n ON n.node_id=h.node_id LEFT JOIN flood_outlines f ON f.outline_id=h.outline_id WHERE n.node_id IS NULL OR f.outline_id IS NULL').fetchone()[0]==0
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='outage_circuit_candidates'").fetchone():
            assert db.execute('''SELECT COUNT(*) FROM outage_circuit_candidates c LEFT JOIN outages o ON o.event_key=c.event_key
                LEFT JOIN nodes n ON n.node_id=c.node_id WHERE o.event_key IS NULL OR n.node_id IS NULL
                OR c.distance_m>1000.0001 OR c.route_fraction<0 OR c.route_fraction>1''').fetchone()[0]==0
            assert db.execute('SELECT COUNT(*) FROM generation_outages WHERE longitude IS NULL OR latitude IS NULL OR physical_failure_verified<>0 OR customer_interruption_verified<>0').fetchone()[0]==0
        metadata={k:json.loads(v) for k,v in db.execute('SELECT * FROM metadata')}
    app.config['GRID_DATABASE']=path
    client=app.test_client()
    overview=client.get('/api/grid/overview?bbox=-180,-90,180,90&zoom=4')
    assert overview.status_code==200 and overview.get_json()['represented_count']==located
    history=client.get('/api/grid/outages/summary').get_json()
    assert history['total']==outages and history['mapped']==mapped
    assert sum(m['count'] for m in history['months'])==outages
    points=client.get('/api/grid/outages/overview?bbox=-180,-90,180,90&zoom=4').get_json()
    assert points['represented_count']==mapped and points['truncated'] is False
    result={'generated_utc':now(),'integrity_checks_passed':True,'retained_nodes':nodes,'located_nodes_represented':located,
            'unlocated_nodes':nodes-located,'outage_reports':outages,'mapped_reports_represented':mapped,
            'unmapped_reports_retained':outages-mapped,'all_months_accounted_for':True,
            'training_eligible_outages':0,'verified_asset_failures':0,'flood_depth_available':False,
            'cleaning':metadata['cleaning'],'node_conditions':metadata['node_conditions'],'recorded_floods':metadata['recorded_floods'],
            'validation_scope':'Source filtering, point geometry, complete overview counts, relationships and evidence integrity; not calibrated breakage probabilities or validated national power flow.'}
    write_json(base/'reports/atlas_validation.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ['cleaning','node_conditions','recorded_floods']},indent=2))


if __name__=='__main__':main()
