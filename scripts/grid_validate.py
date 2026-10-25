"""Validate source integrity, topology references and terrain-data semantics."""
import sqlite3,json,hashlib
import numpy as np,pandas as pd
from grid_common import ROOT,BASE,RAW
from grid_terrain import Terrain50

def main():
 checked=0
 for r in json.loads((BASE/'download_manifest.json').read_text()):
  if r['status']!='downloaded':continue
  path=ROOT/r['path'];h=hashlib.sha256()
  with path.open('rb') as f:
   while block:=f.read(8*1024*1024):h.update(block)
  assert h.hexdigest()==r['sha256'],r['path'];checked+=1
 for report in json.loads((BASE/'reports/cim_report.json').read_text()):assert not report['unresolved_terminals'],report['layer']
 db=sqlite3.connect(BASE/'british_grid.sqlite');integrity=db.execute('PRAGMA integrity_check').fetchone()[0];assert integrity=='ok',integrity;assert not db.execute('PRAGMA foreign_key_check').fetchone()
 assert db.execute("SELECT COUNT(*) FROM nodes WHERE node_type='woodland'").fetchone()[0]==0
 assert db.execute('SELECT COUNT(*) FROM incident_candidates WHERE training_eligible<>0').fetchone()[0]==0
 assert db.execute("SELECT COUNT(*) FROM nodes WHERE terrain_elevation_m IS NOT NULL AND elevation_kind<>'ground_terrain_not_equipment_height'").fetchone()[0]==0
 bad=db.execute("SELECT COUNT(*) FROM edges e JOIN nodes n ON n.node_id=e.to_node WHERE e.source='OSM' AND n.node_type IN ('tower','pole')").fetchone()[0];assert bad==0,'Mechanical supports must not short independent circuits'
 degree=db.execute("SELECT COUNT(*) FROM (SELECT e.from_node,COUNT(DISTINCT e.to_node) AS n FROM edges e JOIN nodes n ON n.node_id=e.from_node WHERE e.source='OSM' AND n.node_type='power_line' GROUP BY e.from_node HAVING n<1 OR n>2)").fetchone()[0];assert degree==0,'Invalid OSM line incidence'
 assert db.execute('SELECT COUNT(*) FROM elevation_profiles WHERE distance_m<0').fetchone()[0]==0
 # Directly check ASCII-grid row orientation and coordinate sampling against the source cells.
 terrain=Terrain50();key=next(iter(terrain.index));meta,array=terrain.tile(key);xy=[(meta['xllcorner']+25,meta['yllcorner']+25),(meta['xllcorner']+meta['ncols']*50-25,meta['yllcorner']+meta['nrows']*50-25)];got=terrain.sample_xy([v[0] for v in xy],[v[1] for v in xy]);np.testing.assert_allclose(got,[array[-1,0],array[0,-1]],equal_nan=True)
 outliers=db.execute('SELECT COUNT(*) FROM nodes WHERE terrain_elevation_m < -500 OR terrain_elevation_m > 2000').fetchone()[0]
 report={'integrity_checks_passed':True,'checked_download_checksums':checked,'sqlite_integrity':integrity,'dangling_references':0,'unresolved_CIM_terminals':0,'OSM_mechanical_support_short_circuits':0,'terrain_cell_orientation_checked':True,'terrain_height_outliers_beyond_minus500_to_2000m':outliers,'training_ready':False,'cascade_ready':False,'validation_scope':'Data integrity and faithful reference handling, not proof of geographic completeness, source truth, power-flow validity, or calibrated failure probabilities.'}
 (BASE/'reports/validation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2));db.close()
if __name__=='__main__':main()
