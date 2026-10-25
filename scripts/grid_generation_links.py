"""Preserve the CIM link from generation units to their electrically connected machines."""
import json,sqlite3
import pandas as pd
from grid_common import BASE,OUT,edge,save_edges

def main():
 rows=[]
 for report in json.loads((BASE/'reports/cim_report.json').read_text()):
  layer=report['layer'];p=OUT/(layer.replace(':','_')+'_all_properties.parquet');d=pd.read_parquet(p,filters=[('class','in',['SynchronousMachine','AsynchronousMachine'])])
  for r in d.itertuples():
   props=json.loads(r.properties_json);unit=props.get('RotatingMachine.GeneratingUnit')
   if unit:rows.append(edge(layer+':'+unit,layer+':'+r.object_id,layer,'generation_unit_has_machine','published_CIM_generation_unit_reference'))
 d=save_edges(rows,'CIM_generation_membership');db=sqlite3.connect('file:'+str(BASE/'british_grid.sqlite')+'?mode=rw',uri=True)
 db.execute("DELETE FROM relationships WHERE relation='generation_unit_has_machine' AND evidence='published_CIM_generation_unit_reference'");d.to_sql('relationships',db,if_exists='append',index=False)
 db.execute("CREATE VIEW IF NOT EXISTS generation_connections AS SELECT r.from_node AS generation_unit_id,r.to_node AS machine_id,e.to_node AS electrical_junction_id,r.source FROM relationships r JOIN edges e ON e.from_node=r.to_node WHERE r.relation='generation_unit_has_machine'");db.commit();assert not db.execute('PRAGMA foreign_key_check').fetchone()
 p=BASE/'reports/coverage.json';report=json.loads(p.read_text());report['counts']['relationships']=db.execute('SELECT COUNT(*) FROM relationships').fetchone()[0];report['generation_unit_machine_links']=len(d);p.write_text(json.dumps(report,indent=2));db.close();print('Generation-unit to machine references',len(d),flush=True)
if __name__=='__main__':main()
