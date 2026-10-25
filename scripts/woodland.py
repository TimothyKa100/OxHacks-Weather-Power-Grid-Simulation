"""Download regional NFI 2024 woodland; derive approximate 30m route exposure."""
from pathlib import Path
import json,hashlib,concurrent.futures
import pandas as pd,numpy as np,requests,shapely
from shapely.geometry import shape
ROOT=Path(__file__).resolve().parents[1];RAW=ROOT/'data/raw/woodland';RAW.mkdir(exist_ok=True)
URL='https://services2.arcgis.com/mHXjwgl3OARRqqD4/ArcGIS/rest/services/National_Forest_Inventory_GB_2024/FeatureServer/0/query'
# BNG envelopes enclose downloaded SSEN overhead routes; queries may include surrounding land.
SCOPES={'south':'350000,70000,570000,250000','north':'0,520000,470000,1230000','south_west_extension':'330000,60000,350000,250000','south_coast_extension':'350000,60000,570000,70000'}
def get(params):
 r=requests.get(URL,params=params,timeout=180);r.raise_for_status();j=r.json()
 if 'error' in j:raise RuntimeError(j['error'])
 return j

def page(task):
 scope,offset=task;p=RAW/f'{scope}_{offset:06}.json';params={'f':'geojson','where':'1=1','geometry':SCOPES[scope],'geometryType':'esriGeometryEnvelope','inSR':27700,'outSR':27700,'spatialRel':'esriSpatialRelIntersects','outFields':'FID,CATEGORY,IFT_IOA,COUNTRY','resultOffset':offset,'resultRecordCount':2000,'orderByFields':'FID','maxAllowableOffset':5,'geometryPrecision':1}
 if not p.exists():p.write_text(json.dumps(get(params)))
 j=json.loads(p.read_text());print(scope,offset,len(j['features']),flush=True)
 return {'path':str(p.relative_to(ROOT)),'url':URL,'params':params,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'rows':len(j['features'])}
def derive(manifest):
 rows=[];seen=set()
 for item in manifest:
  for f in json.loads((ROOT/item['path']).read_text())['features']:
   if f['id'] in seen:continue
   seen.add(f['id']);g=shapely.make_valid(shape(f['geometry']));rows.append({**f['properties'],'geometry_bng_wkb':g.wkb})
 woods=pd.DataFrame(rows);woods.to_parquet(ROOT/'data/processed/woodland_2024.parquet',index=False)
 # Include all NFI categories; this is proximity to mapped woodland land, not tree height.
 wg=shapely.from_wkb(woods.geometry_bng_wkb.to_numpy());tree=shapely.STRtree(wg)
 seg=pd.read_parquet(ROOT/'data/processed/overhead_segments.parquet');lines=shapely.from_wkt(seg.geometry_bng_wkt.to_numpy());exposed=np.zeros(len(lines))
 for i,line in enumerate(lines):
  if line is None or line.is_empty:exposed[i]=np.nan;continue
  idx=tree.query(line.buffer(30),predicate='intersects')
  if len(idx):exposed[i]=line.intersection(shapely.union_all(shapely.buffer(wg[idx],30))).length/1000
 seg['nfi_2024_within_30m_km']=exposed
 seg[['segment_id','circuit_key','nfi_2024_within_30m_km']].to_parquet(ROOT/'data/processed/segment_woodland_exposure.parquet',index=False)
 c=pd.read_parquet(ROOT/'data/processed/circuits.parquet');s=seg.groupby('circuit_key').nfi_2024_within_30m_km.sum();c['woodland_exposure_fraction']=c.circuit_key.map(s)/c.overhead_length_km;c['woodland_source']='NFI2024_5m_generalisation_30m_buffer';c.to_parquet(ROOT/'data/processed/circuits.parquet',index=False)
 print('Woodland polygons',len(woods),flush=True)

if __name__=='__main__':
 import argparse
 ap=argparse.ArgumentParser();ap.add_argument('--derive-only',action='store_true');args=ap.parse_args()
 if args.derive_only:
  manifest=json.loads((ROOT/'data/woodland_manifest.json').read_text())
 else:
  tasks=[]
  for scope,box in SCOPES.items():
   j=get({'f':'json','where':'1=1','geometry':box,'geometryType':'esriGeometryEnvelope','inSR':27700,'returnCountOnly':'true'});print(scope,j,flush=True);tasks.extend((scope,i) for i in range(0,j['count'],2000))
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:manifest=list(pool.map(page,tasks))
  (ROOT/'data/woodland_manifest.json').write_text(json.dumps(manifest,indent=2))
 derive(manifest)
