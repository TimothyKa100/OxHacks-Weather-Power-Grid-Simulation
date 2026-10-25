"""Preserve native half-hourly demand; do not invent units from contradictory documentation."""
from pathlib import Path
import json,zipfile
import pandas as pd,py7zr
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/processed'
def main():
 frames=[]
 for r in json.load(open(ROOT/'data/manifest.json')):
  if 'bressay' not in r['name'].lower():continue
  p=ROOT/r['path']
  if p.suffix=='.csv':
   d=pd.read_csv(p,dtype={'nrn':str}).rename(columns={'nrn':'network_reference','READING_DATE_TIME':'timestamp_raw','sum(TOTAL_CONSUMPTION_kWh)':'consumption_reported'})
   d['unit_declared']='kWh';d['unit_status']='publisher_conversion_description_inconsistent';d['meter_count']=pd.NA
  elif p.suffix=='.zip':
   with zipfile.ZipFile(p) as z:
    name=next(n for n in z.namelist() if n.endswith('.7z'));inner=p.with_suffix('.7z');inner.write_bytes(z.read(name))
   dest=p.parent/'extracted';dest.mkdir(exist_ok=True)
   with py7zr.SevenZipFile(inner) as z:
    names=[n for n in z.getnames() if n.endswith('.csv')]
    if not all((dest/n).resolve().is_relative_to(dest.resolve()) for n in names):raise ValueError('Unsafe archive path')
    if not all((dest/n).exists() for n in names):z.extract(path=dest,targets=names)
   d=pd.concat([pd.read_csv(dest/n,dtype={'dataset_id':str}) for n in names],ignore_index=True).rename(columns={'dataset_id':'network_reference','Total_Consumption':'consumption_reported','mpan_count':'meter_count','consumption_datetime':'timestamp_raw'})
   d['unit_declared']='not_stated_in_file';d['unit_status']='requires_publisher_confirmation'
  else:continue
  d['timestamp_local_unconfirmed_timezone']=pd.to_datetime(d.timestamp_raw,format='mixed',errors='coerce');d['interval_minutes']=30;d['source_resource']=r['resource_id'];d['operator']='SHEPD'
  d=d.drop_duplicates();d['duplicate_network_timestamp']=d.duplicated(['network_reference','timestamp_raw'],keep=False)
  d['consumption_reported']=pd.to_numeric(d.consumption_reported,errors='coerce');d['meter_count']=pd.to_numeric(d.meter_count,errors='coerce').astype('Int64')
  frames.append(d)
 allrows=pd.concat(frames,ignore_index=True);allrows.to_parquet(P/'bressay_demand_native.parquet',index=False);print('Native half-hour records',len(allrows))
if __name__=='__main__':main()
