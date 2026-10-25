"""Build schema inventory, coverage maps and an explicit supervised-training readiness report."""
from pathlib import Path
import json,datetime
import pandas as pd,numpy as np,pyarrow.parquet as pq
from pyproj import Geod
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/processed'
def main():
 config=json.loads((ROOT/'data/weather_config.json').read_text());spacing=config['spacing_degrees']
 c=pd.read_parquet(P/'circuit_roster.parquet');gis=pd.read_parquet(P/'circuits.parquet').set_index('circuit_key')
 c['woodland_exposure_fraction']=c.gis_circuit_key_candidate.map(gis.woodland_exposure_fraction)
 c.to_parquet(P/'circuit_roster.parquet',index=False)
 weather=json.loads((ROOT/'data/weather_manifest.json').read_text());available={r['weather_key']:r for r in weather if r['status']=='downloaded' and (P/'weather'/f"{r['weather_key']}.parquet").exists()}
 # Roster locations are representative supply-area points, not failed-equipment coordinates.
 cw=c[['circuit_key','latitude','longitude']].copy();cw['sample_latitude']=(cw.latitude/spacing).round()*spacing;cw['sample_longitude']=(cw.longitude/spacing).round()*spacing
 cw['weather_key']=cw.apply(lambda r:f'{r.sample_latitude+0.0:.2f}_{r.sample_longitude+0.0:.2f}',axis=1);cw['weather_available']=cw.weather_key.isin(available)
 cw['distance_to_request_point_km']=Geod(ellps='WGS84').inv(cw.longitude,cw.latitude,cw.sample_longitude,cw.sample_latitude)[2]/1000
 cw['weather_grid_elevation_m']=cw.weather_key.map({k:v['elevation_m'] for k,v in available.items()})
 cw['association']=f'approximate_supply_area_representative_to_{spacing}degree_lattice';cw.to_parquet(P/'roster_weather_crosswalk.parquet',index=False)
 schema={}
 for path in P.rglob('*.parquet'):
  f=pq.ParquetFile(path);schema[str(path.relative_to(P))]={'rows':f.metadata.num_rows,'bytes':path.stat().st_size,'columns':{field.name:str(field.type) for field in f.schema_arrow}}
 (P/'schema_inventory.json').write_text(json.dumps(schema,indent=2))
 quality=json.loads((P/'quality_report.json').read_text());enrichment=json.loads((P/'enrichment_report.json').read_text());faults=pd.read_parquet(P/'fault_candidates_UNVERIFIED.parquet')
 manifest=json.loads((ROOT/'data/manifest.json').read_text())
 report={'generated_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'supervised_training_ready':False,'cascade_ready':False,'raw_bytes':sum(p.stat().st_size for p in (ROOT/'data/raw').rglob('*') if p.is_file()),'processed_bytes':sum(p.stat().st_size for p in P.rglob('*') if p.is_file()),'ssen_downloaded_resources':sum(r['status']=='downloaded' for r in manifest),'ssen_failed_resources':[r for r in manifest if r['status']!='downloaded'],'roster_circuits':len(c),'overhead_circuits':len(gis),'overhead_segments':quality['overhead_segments'],'candidate_incidents':len(faults),'verified_unplanned_incidents':quality['unplanned_incidents_VERIFIED'],'excluded_possible_operational_incidents':quality['excluded_incident_audit'],'candidate_feeder_match_fraction':enrichment['fault_candidate_feeder_match_fraction'],'fault_period':[quality['incident_start'],quality['incident_end']],'ambiguous_or_invalid_utc_times':int(faults.reported_at_utc_assuming_london.isna().sum()),'duplicate_candidate_event_keys':int(faults.event_key_duplicate.sum()),'weather_locations_requested_in_plan':int(pd.read_parquet(P/'circuit_weather_crosswalk.parquet').weather_key.nunique()),'weather_locations_pending':sorted(set(pd.read_parquet(P/'circuit_weather_crosswalk.parquet').weather_key)-set(available)),'weather_locations_downloaded':len(available),'weather_hourly_rows':sum(r.get('rows',0) for r in available.values()),'weather_incomplete_requests':[r for r in weather if r['status']!='downloaded'],'roster_circuits_with_available_weather':int(cw.weather_available.sum()),'roster_weather_fraction':float(cw.weather_available.mean()),'roster_feature_nonnull_fraction':{col:float(c[col].notna().mean()) for col in ['voltage_kv','overhead_length_km','woodland_exposure_fraction','underground_length_km','commissioning_year','customer_count','r_ohm','x_ohm','thermal_rating_mva','load_mw','generation_mw']},'electrical_lines':enrichment['SEPD_electrical_lines']+enrichment['SHEPD_electrical_lines'],'electrical_terminal_references_resolve':enrichment['SEPD_unresolved_terminal_nodes']==0 and enrichment['SHEPD_unresolved_terminal_nodes']==0,'blockers':['No explicit planned/unplanned classification in fault exports; all incident labels quarantined.','No circuit restoration/availability or verified reporting-coverage intervals; cannot construct trustworthy negative exposure.','NRN matching is a candidate formatting crosswalk, not operator-certified.','No confirmed feeder/GIS to electrical-model identifier crosswalk.','Historical asset topology/maintenance/commissioning states incomplete.','Missing circuit impedance/rating/loading fields remain null; separate electrical model is not joined.','Weather is coarse retrospective reanalysis; see actual download coverage.','Lightning, inundation depth, detailed terrain and asset age/condition missing.']}
 for name in ['connected_generation_snapshots','woodland_2024','bressay_demand_native']:
  if name+'.parquet' in schema:report[name+'_rows']=schema[name+'.parquet']['rows']
 report['blockers']=[b.replace('Lightning, inundation depth, detailed terrain and asset age/condition missing.','Lightning, inundation depth and asset age/condition missing; GB terrain is available in data/grid and joined in the feeder research pipeline.') for b in report['blockers']]
 report['weather_scope_note']='Roster representative-point coverage is distinct from complete route weather; see data/failure_model/*/inventory_report.json.'
 (P/'delivery_report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
