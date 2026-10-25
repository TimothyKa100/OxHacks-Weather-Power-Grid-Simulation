"""Supply hierarchy, explicit electrical connectivity, and feature availability audit."""
from pathlib import Path
import json
import pandas as pd,numpy as np,shapely
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/processed';R=ROOT/'data/raw'
def main():
 rows=[];links=[]
 for path in (R/'secondary-supply-areas').glob('*.csv'):
  d=pd.read_csv(path,dtype=str,keep_default_na=False);op='SHEPD' if 'gsp_nrn' in d else 'SEPD'
  d['nrn_key']=d.primary_nrn_split.map(lambda v:str(int(v)) if v.isdigit() else v)+':'+d.hv_feeder_nrn.map(lambda v:str(int(v)) if v.isdigit() else v)
  for key,g in d.groupby('nrn_key'):
   union=shapely.union_all(shapely.make_valid(shapely.from_wkt(g.geometry.to_numpy())));point=union.representative_point()
   rows.append({'circuit_key':op+':FEEDER:'+key,'operator':op,'nrn_candidate_key':key,'primary_nrn':g.primary_nrn_split.iloc[0],'hv_feeder_nrn':g.hv_feeder_nrn.iloc[0],'primary_name':g.primary_name_2025.iloc[0],'primary_voltage_step_raw':g['voltage_step' if op=='SHEPD' else 'primary_voltage_step'].iloc[0],'gsp_names':json.dumps(sorted(g.gsp_name.unique())),'secondary_supply_area_count':len(g),'longitude':point.x,'latitude':point.y,'source_resource':path.stem,'geometry_wgs84_wkb':union.wkb})
   for _,s in g.iterrows():links.append({'operator':op,'circuit_key':op+':FEEDER:'+key,'secondary_nrn':s.nrn_10_digit,'primary_nrn':s.primary_nrn_split,'gsp_name':s.gsp_name,'source_resource':path.stem,'relation':'published_supply_hierarchy_not_switch_topology'})
 c=pd.DataFrame(rows);steps=c.primary_voltage_step_raw.str.extract(r'^(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)$');c['primary_hv_voltage_kv']=pd.to_numeric(steps[0],errors='coerce');c['primary_lv_voltage_kv']=pd.to_numeric(steps[1],errors='coerce');pd.DataFrame(links).to_parquet(P/'supply_hierarchy.parquet',index=False)
 overhead=pd.read_parquet(P/'circuits.parquet');overhead['crosswalk_key']=overhead.operator_candidate+':'+overhead.nrn_candidate_key
 count=overhead.crosswalk_key.value_counts();lookup=overhead[overhead.crosswalk_key.map(count).eq(1)].set_index('crosswalk_key')
 c['crosswalk_key']=c.operator+':'+c.nrn_candidate_key;c['gis_circuit_key_candidate']=c.crosswalk_key.map(lookup.circuit_key)
 for col in ['overhead_length_km','voltage_kv','woodland_exposure_fraction','commissioning_year','customer_count','underground_length_km','overhead_share','r_ohm','x_ohm','thermal_rating_mva','load_mw','generation_mw','elevation_m','flood_depth_m','lightning_strikes']:
  c[col]=c.crosswalk_key.map(lookup[col])
 c['training_ready']=False;c['electrical_model_join_status']='missing_crosswalk';c['snapshot_historical_validity']='current_inventory_not_historical_configuration';c.to_parquet(P/'circuit_roster.parquet',index=False)
 f=pd.read_parquet(P/'fault_candidates_UNVERIFIED.parquet');look=c.set_index('crosswalk_key').circuit_key
 f['feeder_key_candidate']=(f.operator+':'+f.nrn_candidate_key).map(look);f.to_parquet(P/'fault_candidates_UNVERIFIED.parquet',index=False)
 counts={'roster_circuits':len(c),'fault_candidates_with_feeder_match':int(f.feeder_key_candidate.notna().sum()),'fault_candidate_feeder_match_fraction':float(f.feeder_key_candidate.notna().mean()),'roster_with_gis_match':int(c.gis_circuit_key_candidate.notna().sum())}
 for op in ['SEPD','SHEPD']:
  t=pd.read_parquet(P/f'cim_{op}_Terminal.parquet');t=t.rename(columns={'object_id':'terminal_id','Terminal.ConductingEquipment':'equipment_id','Terminal.ConnectivityNode':'connectivity_node_id'});t['operator']=op
  nodes=pd.read_parquet(P/f'cim_{op}_ConnectivityNode.parquet');t['node_reference_resolved']=t.connectivity_node_id.isin(nodes.object_id)
  t.to_parquet(P/f'electrical_terminals_{op}.parquet',index=False);counts[op+'_unresolved_terminal_nodes']=int((~t.node_reference_resolved).sum())
  line=pd.read_parquet(P/f'cim_{op}_ACLineSegment.parquet');v=pd.read_parquet(P/f'cim_{op}_BaseVoltage.parquet').set_index('object_id')['BaseVoltage.nominalVoltage']
  line['voltage_kv']=pd.to_numeric(line['ConductingEquipment.BaseVoltage'].map(v),errors='coerce')
  for source,target in [('ACLineSegment.r','r_ohm'),('ACLineSegment.x','x_ohm'),('ACLineSegment.bch','b_siemens'),('Conductor.length','length_km')]:line[target]=pd.to_numeric(line[source],errors='coerce')
  line['zero_impedance_requires_topology_processing']=line.r_ohm.eq(0)&line.x_ohm.eq(0)
  group=t.groupby('equipment_id').connectivity_node_id.agg(list);line['terminal_node_ids']=line.object_id.map(group).map(lambda v:json.dumps(v) if isinstance(v,list) else None)
  line['operator']=op;line['fault_inventory_crosswalk']='unverified';line.to_parquet(P/f'electrical_lines_{op}.parquet',index=False)
  counts[op+'_electrical_lines']=len(line)
 (P/'enrichment_report.json').write_text(json.dumps(counts,indent=2));print(counts)
if __name__=='__main__':main()
