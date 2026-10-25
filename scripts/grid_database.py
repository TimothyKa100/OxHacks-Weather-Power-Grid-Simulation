"""Package the normalized layers as one indexed SQLite asset/graph database."""
from pathlib import Path
import sqlite3,json,datetime
import pandas as pd,pyarrow.parquet as pq
from grid_common import ROOT,BASE,OUT,node
EXTRA_NUM=['bbox_min_lon','bbox_min_lat','bbox_max_lon','bbox_max_lat','terrain_elevation_m','elevation_resolution_m','terrain_min_m','terrain_max_m','terrain_mean_m','terrain_valid_samples','terrain_requested_samples','terrain_profile_coverage']
EXTRA_TEXT=['elevation_source','elevation_kind']
NUM={'latitude','longitude','voltage_kv','r_ohm','x_ohm','b_siemens','capacity_mva','generation_capacity_mw','length_km'}|set(EXTRA_NUM)
COLS=list(node('','',''))+EXTRA_NUM+EXTRA_TEXT

def load_parquet(db,path,table,columns=None):
 total=0
 for batch in pq.ParquetFile(path).iter_batches(batch_size=10000):
  frame=batch.to_pandas()
  if columns is not None and 'geometry_wgs84_wkb' in frame:
   import shapely,numpy as np
   bounds=shapely.bounds(shapely.from_wkb(frame.geometry_wgs84_wkb.to_numpy()))
   for i,col in enumerate(['bbox_min_lon','bbox_min_lat','bbox_max_lon','bbox_max_lat']):frame[col]=bounds[:,i]
   frame.bbox_min_lon=frame.bbox_min_lon.fillna(frame.longitude);frame.bbox_max_lon=frame.bbox_max_lon.fillna(frame.longitude);frame.bbox_min_lat=frame.bbox_min_lat.fillna(frame.latitude);frame.bbox_max_lat=frame.bbox_max_lat.fillna(frame.latitude)
   frame=frame.reindex(columns=columns)
  elif columns is not None:frame=frame.reindex(columns=columns)
  frame.to_sql(table,db,if_exists='append',index=False,chunksize=2000);total+=len(frame)
 return total

def main():
 import argparse
 ap=argparse.ArgumentParser();ap.add_argument('--resume-build',action='store_true');args=ap.parse_args()
 path=BASE/'british_grid.sqlite';temporary=BASE/'british_grid.build.sqlite'
 if temporary.exists() and not args.resume_build:temporary.unlink()
 db=sqlite3.connect(temporary);db.execute('PRAGMA journal_mode=OFF');db.execute('PRAGMA synchronous=OFF')
 defs=[f'"{c}" '+('BLOB' if c=='geometry_wgs84_wkb' else 'REAL' if c in NUM else 'TEXT')+(' PRIMARY KEY' if c=='node_id' else '') for c in COLS]
 db.execute('CREATE TABLE IF NOT EXISTS nodes ('+','.join(defs)+')');edgecols=['from_node','to_node','source','relation','evidence']
 for table in ['edges','relationships','candidate_edges']:
  db.execute(f'CREATE TABLE IF NOT EXISTS {table} (from_node TEXT REFERENCES nodes(node_id),to_node TEXT REFERENCES nodes(node_id),source TEXT,relation TEXT,evidence TEXT)')
 layers=[]
 for file in sorted(OUT.glob('*_nodes.parquet')):
  if file.name in {'NI_power_nodes.parquet','excluded_non_grid_nodes.parquet'}:continue
  count=pq.ParquetFile(file).metadata.num_rows if args.resume_build else load_parquet(db,file,'nodes',COLS);layers.append({'file':str(file.relative_to(ROOT)),'nodes':count});print('Database nodes',file.stem,count,flush=True)
 if args.resume_build:
  assert db.execute('SELECT COUNT(*) FROM nodes').fetchone()[0]==sum(r['nodes'] for r in layers)
  assert all(db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]==0 for t in ['edges','relationships','candidate_edges'])
 for file in sorted(OUT.glob('*_edges.parquet')):
  if file.name=='NI_power_edges.parquet':continue
  table='candidate_edges' if '_candidate_' in file.name else 'relationships' if any(w in file.name for w in ['membership','containment','hierarchy']) else 'edges';count=load_parquet(db,file,table,edgecols);print('Database',table,file.stem,count,flush=True)
 for file in sorted(OUT.glob('*_elevation_profiles.parquet')):
  load_parquet(db,file,'elevation_profiles')
 for file in sorted(OUT.glob('*_seasonal_ratings.parquet')):
  load_parquet(db,file,'seasonal_ratings')
 # Attach existing incident evidence at its supported feeder resolution; none is promoted to a certified failure.
 incidents=pd.read_parquet(ROOT/'data/processed/fault_candidates_UNVERIFIED.parquet');keep=['event_key','operator','reported_at_local','reported_at_utc_assuming_london','feeder_key_candidate','cause','damage','planned_status','training_eligible']
 incidents[keep].to_sql('incident_candidates',db,index=False,if_exists='replace')
 weather=pd.read_parquet(ROOT/'data/processed/roster_weather_crosswalk.parquet');weather.to_sql('existing_feeder_weather_links',db,index=False,if_exists='replace')
 for table in ['edges','relationships','candidate_edges']:
  db.execute(f'CREATE INDEX {table}_from_idx ON {table}(from_node)');db.execute(f'CREATE INDEX {table}_to_idx ON {table}(to_node)')
 db.execute('CREATE INDEX nodes_type_source_idx ON nodes(node_type,source)');db.execute('CREATE INDEX nodes_source_id_idx ON nodes(source,source_asset_id)')
 db.execute('CREATE VIRTUAL TABLE spatial_index USING rtree(id,min_lon,max_lon,min_lat,max_lat)');db.execute('INSERT INTO spatial_index SELECT rowid,bbox_min_lon,bbox_max_lon,bbox_min_lat,bbox_max_lat FROM nodes WHERE bbox_min_lon IS NOT NULL AND bbox_min_lat IS NOT NULL')
 db.execute("""CREATE VIEW conducting_edges AS
 SELECT e.* FROM edges e JOIN nodes a ON a.node_id=e.from_node JOIN nodes b ON b.node_id=e.to_node
 WHERE (a.node_type NOT IN ('breaker','switch','disconnector') OR json_extract(a.properties_json,'$."Switch.normalOpen"') IN ('false',0))
 AND (b.node_type NOT IN ('breaker','switch','disconnector') OR json_extract(b.properties_json,'$."Switch.normalOpen"') IN ('false',0))""")
 metadata={'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'crs':'EPSG:4326 for geometry WKB and lon/lat; terrain profiles EPSG:27700','representation':'Physical line sections are nodes; edges encode terminal incidence. Keep source/topology layers separate until identity crosswalks are confirmed.','coverage':'Public British grid collection, not a complete validated national electrical model.','generation_assumption':'Supply adequacy may be assumed by a later simulation. No dispatch or demand balance is simulated here.','historical_validity':'Current/2026 snapshots; not verified historical configurations.','elevation':'OS Terrain 50 ground terrain, not conductor height or asset floor level. 250m route-profile sample spacing on a 50m DEM.','duplicate_representations':'Do not count cross-source asset records as unique physical assets or combine parallel model layers as one electrical grid.','weather':'Existing SSEN weather links retained. The nationwide sampling plan is a request plan, not downloaded weather.','training_ready':False,'cascade_ready':False}
 db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT)');db.executemany('INSERT INTO metadata VALUES (?,?)',[(k,json.dumps(v)) for k,v in metadata.items()]);db.commit()
 finalize(db,path,temporary,layers)

def finalize(db,path,temporary,layers):
 metadata={key:json.loads(value) for key,value in db.execute('SELECT key,value FROM metadata')}
 errors=db.execute('PRAGMA foreign_key_check').fetchmany(10)
 if errors:raise RuntimeError('Dangling graph references: '+str(errors))
 report={'metadata':metadata,'layers':layers,'node_types':dict(db.execute('SELECT node_type,COUNT(*) FROM nodes GROUP BY node_type')),'counts':{t:db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ['nodes','edges','relationships','candidate_edges','elevation_profiles','seasonal_ratings','incident_candidates']},'located_nodes':db.execute('SELECT COUNT(*) FROM nodes WHERE longitude IS NOT NULL AND latitude IS NOT NULL').fetchone()[0],'nodes_with_terrain_elevation':db.execute('SELECT COUNT(*) FROM nodes WHERE terrain_elevation_m IS NOT NULL').fetchone()[0],'power_lines_with_electrical_impedance':db.execute("SELECT COUNT(*) FROM nodes WHERE node_type='power_line' AND r_ohm IS NOT NULL AND x_ohm IS NOT NULL").fetchone()[0],'power_lines_with_route_elevation_profiles':db.execute("SELECT COUNT(*) FROM nodes WHERE node_type='power_line' AND terrain_valid_samples>0").fetchone()[0]}
 plan=pd.read_sql_query('SELECT ROUND(latitude*4)/4.0 AS latitude,ROUND(longitude*4)/4.0 AS longitude,COUNT(*) AS asset_records FROM nodes WHERE latitude IS NOT NULL AND longitude IS NOT NULL GROUP BY 1,2',db);plan['status']='not_downloaded_by_grid_pipeline';plan.to_parquet(OUT/'national_weather_sampling_plan.parquet',index=False)
 db.close();temporary.replace(path);(BASE/'reports/coverage.json').write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ['layers','metadata']},indent=2),flush=True)
if __name__=='__main__':main()
