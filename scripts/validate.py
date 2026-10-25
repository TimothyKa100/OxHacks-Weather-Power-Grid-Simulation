"""Integrity checks on the delivered collection, distinct from training readiness."""
from pathlib import Path
import json,hashlib
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/processed'
def main():
 checked=0
 for name in ['manifest.json','weather_manifest.json','woodland_manifest.json']:
  for row in json.loads((ROOT/'data'/name).read_text()):
   if 'sha256' not in row:continue
   assert hashlib.sha256((ROOT/row['path']).read_bytes()).hexdigest()==row['sha256'],row['path'];checked+=1
 c=pd.read_parquet(P/'circuit_roster.parquet');assert c.circuit_key.is_unique
 g=pd.read_parquet(P/'circuits.parquet');assert g.circuit_key.is_unique
 assert g.woodland_exposure_fraction.dropna().between(-1e-9,1+1e-9).all()
 seg=pd.read_parquet(P/'overhead_segments.parquet');wood=pd.read_parquet(P/'segment_woodland_exposure.parquet');assert len(wood)==len(seg)
 assert seg.segment_id.is_unique
 lengths=seg[['segment_id','length_km']].merge(wood,on='segment_id',validate='one_to_one');assert (lengths.nfi_2024_within_30m_km<=lengths.length_km+1e-8).all()
 f=pd.read_parquet(P/'fault_candidates_UNVERIFIED.parquet');excluded=pd.read_parquet(P/'excluded_incident_audit.parquet');assert len(f)+len(excluded)==json.loads((P/'quality_report.json').read_text())['planned_status_missing']
 assert not f.training_eligible.any();assert pd.read_parquet(P/'unplanned_incidents_VERIFIED.parquet').empty
 snap=pd.read_parquet(P/'feature_snapshot.parquet');assert snap.circuit_key.is_unique and len(snap)==len(c);assert not snap.training_ready.any();assert not snap.target_available.any();assert not {'cause','damage','avg_time_off_mins'}&set(snap.columns)
 ratings=0
 for op in ['SEPD','SHEPD']:
  t=pd.read_parquet(P/f'electrical_terminals_{op}.parquet');assert t.node_reference_resolved.all()
  r=pd.read_parquet(P/f'equipment_seasonal_ratings_{op}.parquet');assert r.equipment_id.notna().all();assert r.rating.notna().all();assert r.unit.isin(['A','MVA']).all();ratings+=len(r)
 gen=pd.read_parquet(P/'connected_generation_snapshots.parquet');assert gen.connection_status.eq('CONNECTED').all()
 report={'integrity_checks_passed':True,'checksummed_resources':checked,'feeder_rows':len(c),'snapshot_rows':len(snap),'candidate_incident_rows':len(f),'woodland_segment_rows':len(wood),'seasonal_equipment_ratings':ratings,'supervised_training_ready':False,'note':'Integrity success does not resolve the documented label, coverage and crosswalk blockers.'}
 (P/'validation_report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
