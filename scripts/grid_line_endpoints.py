"""Expose known electrical-line terminal locations and ground heights without guessing routes."""
import sqlite3,json
import pandas as pd
from grid_common import BASE,OUT

def main():
 db=sqlite3.connect('file:'+str(BASE/'british_grid.sqlite')+'?mode=rw',uri=True)
 query="""SELECT line.node_id AS line_node_id, e.to_node AS terminal_junction_id,
 line.source, line.voltage_kv, line.r_ohm, line.x_ohm,
 terminal.longitude, terminal.latitude, terminal.terrain_elevation_m,
 terminal.location_method AS terminal_location_method,
 'published_terminal_reference_and_documented_host_substation_location' AS evidence,
 'terminal_ground_only_not_line_route_profile' AS terrain_coverage_kind
 FROM edges e JOIN nodes line ON line.node_id=e.from_node
 JOIN nodes terminal ON terminal.node_id=e.to_node
 WHERE line.node_type='power_line' AND line.r_ohm IS NOT NULL
 AND terminal.longitude IS NOT NULL AND terminal.latitude IS NOT NULL
 """
 d=pd.read_sql_query(query,db);d.to_parquet(OUT/'electrical_line_endpoint_terrain.parquet',index=False);d.to_sql('electrical_line_endpoint_terrain',db,if_exists='replace',index=False);db.execute('CREATE INDEX IF NOT EXISTS endpoint_line_idx ON electrical_line_endpoint_terrain(line_node_id)')
 db.execute('CREATE VIEW IF NOT EXISTS electrical_line_terminal_elevation_summary AS SELECT line_node_id,source,COUNT(*) AS located_terminal_records,MIN(terrain_elevation_m) AS terminal_ground_min_m,MAX(terrain_elevation_m) AS terminal_ground_max_m FROM electrical_line_endpoint_terrain GROUP BY line_node_id,source');db.commit();db.close()
 p=BASE/'reports/coverage.json';r=json.loads(p.read_text());r['electrical_line_endpoint_records']=len(d);r['electrical_lines_with_located_terminal_evidence']=int(d.line_node_id.nunique());p.write_text(json.dumps(r,indent=2));print('Electrical line endpoint records',len(d),'lines',d.line_node_id.nunique(),flush=True)
if __name__=='__main__':main()
