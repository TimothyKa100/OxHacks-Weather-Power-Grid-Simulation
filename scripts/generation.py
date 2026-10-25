"""Normalize connected generation snapshots; never treat planned projects as supply."""
from pathlib import Path
import json,re
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/processed'
FIELDS={'Unique ID':'generator_key','Connection Status':'connection_status','Customer Site':'site_name','Primary':'primary_name','Grid Supply Point':'gsp_name','Licence Area':'operator_name','Date Connected':'connected_date','Already connected Registered Capacity (MW)':'connected_capacity_mw','Maximum Export Capacity (MW)':'max_export_mw','Maximum Import Capacity (MW)':'max_import_mw','Latitude':'latitude','Longitude':'longitude','Energy Source 1':'energy_source','Energy Conversion Technology 1':'technology','Storage Capacity 1 (MWh)':'storage_mwh','Point of Connection (POC) Voltage (kV)':'voltage_kv'}
def norm(d,r,sheet):
 d.columns=[' '.join(str(c).split()) for c in d.columns];d=d.loc[:,~d.columns.duplicated()];out=pd.DataFrame(index=d.index)
 for source,target in FIELDS.items():out[target]=d[source] if source in d else pd.NA
 out=out[out.connection_status.str.upper().eq('CONNECTED')].copy();out=out.astype('string').replace(['DATA NOT AVAILABLE','DATA NOT APPLICABLE',''],pd.NA)
 for c in ['connected_capacity_mw','max_export_mw','max_import_mw','latitude','longitude','storage_mwh','voltage_kv']:out[c]=pd.to_numeric(out[c],errors='coerce')
 out['connected_date_raw']=out.connected_date.astype('string');out['connected_date']=pd.to_datetime(out.connected_date,format='mixed',dayfirst=True,errors='coerce')
 out['source_resource']=r['resource_id'];out['snapshot_title']=r['name'];out['source_sheet']=sheet;return out
if __name__=='__main__':
 frames=[]
 for r in json.load(open(ROOT/'data/manifest.json')):
  if r['dataset']!='embedded_capacity_register' or not r['path'].endswith('.xlsx'):continue
  x=pd.ExcelFile(ROOT/r['path'])
  for sheet in x.sheet_names:
   if not sheet.lower().startswith('register part 1'):continue
   frames.append(norm(pd.read_excel(x,sheet_name=sheet,header=1,dtype=str),r,sheet))
  print(r['name'],flush=True)
 pd.concat(frames,ignore_index=True).to_parquet(P/'connected_generation_snapshots.parquet',index=False)
