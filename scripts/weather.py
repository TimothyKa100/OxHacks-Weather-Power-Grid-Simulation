"""Hourly ERA5 archive and causal exposure features. Resumable; respects rate limits."""
from pathlib import Path
import argparse,json,time,hashlib
import requests,pandas as pd,numpy as np
ROOT=Path(__file__).resolve().parents[1]
VARIABLES='wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m,precipitation,snow_depth,soil_moisture_0_to_7cm'
THRESHOLDS={'gust_ge_80_kmh':('wind_gusts_10m',80,'ge'),'gust_ge_100_kmh':('wind_gusts_10m',100,'ge'),'gust_ge_120_kmh':('wind_gusts_10m',120,'ge'),'heat_ge_30_c':('temperature_2m',30,'ge'),'cold_le_minus5_c':('temperature_2m',-5,'le'),'rain_ge_10_mm_h':('precipitation',10,'ge')}
def features(d):
 d=d.sort_values('time').copy();d['time']=pd.to_datetime(d.time,utc=True)
 if not d.time.diff().dropna().eq(pd.Timedelta(hours=1)).all():raise ValueError('Non-hourly or duplicate weather timestamps')
 for name,(col,threshold,op) in THRESHOLDS.items():
  # Shift every exposure: features at t only see samples strictly before t.
  x=d[col];above=(x.ge(threshold) if op=='ge' else x.le(threshold)).where(x.notna()).astype(float)
  past=above.shift(1)
  for window in (6,24,72):d[f'{name}_sample_hours_prev_{window}h']=past.rolling(window,min_periods=window).sum()
  excess=((x-threshold) if op=='ge' else (threshold-x)).clip(lower=0).shift(1)
  d[f'{name}_excess_prev_24h']=excess.rolling(24,min_periods=24).sum()
 d['gust_max_prev_24h_kmh']=d.wind_gusts_10m.shift(1).rolling(24,min_periods=24).max()
 d['precipitation_prev_24h_mm']=d.precipitation.shift(1).rolling(24,min_periods=24).sum()
 return d
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--spacing',type=float,default=0.5);ap.add_argument('--start',default='2015-04-01');ap.add_argument('--end',default='2026-10-18');args=ap.parse_args()
 (ROOT/'data/weather_config.json').write_text(json.dumps({'spacing_degrees':args.spacing,'start':args.start,'end':args.end,'model':'era5','variables':VARIABLES},indent=2))
 raw=ROOT/'data/raw/weather';raw.mkdir(exist_ok=True);out=ROOT/'data/processed/weather';out.mkdir(exist_ok=True)
 c=pd.read_parquet(ROOT/'data/processed/circuits.parquet');c['sample_latitude']=(c.latitude/args.spacing).round()*args.spacing;c['sample_longitude']=(c.longitude/args.spacing).round()*args.spacing
 c['weather_key']=c.apply(lambda r:f'{r.sample_latitude:.2f}_{r.sample_longitude:.2f}',axis=1)
 c[['circuit_key','weather_key','sample_latitude','sample_longitude']].to_parquet(ROOT/'data/processed/circuit_weather_crosswalk.parquet',index=False)
 rows=[]
 for key,g in c.groupby('weather_key'):
  lat,lon=g.iloc[0][['sample_latitude','sample_longitude']];p=raw/f'{key}_{args.start}_{args.end}.json'
  params=dict(latitude=lat,longitude=lon,start_date=args.start,end_date=args.end,hourly=VARIABLES,models='era5',timezone='UTC',wind_speed_unit='kmh')
  row={'weather_key':key,'params':params,'license':'CC-BY-4.0','source':'https://archive-api.open-meteo.com/v1/archive','path':str(p.relative_to(ROOT))}
  try:
   if not p.exists():
    response=requests.get(row['source'],params=params,timeout=180)
    for attempt in range(4):
     if response.status_code!=429 or 'Minutely' not in response.text:break
     print('Waiting for weather minute quota',flush=True);time.sleep(20);response=requests.get(row['source'],params=params,timeout=180)
    if response.status_code==429:
     row.update(status='rate_limited',error=response.text);rows.append(row);print('Rate limited; saved progress. Rerun later.',flush=True);break
    response.raise_for_status();p.write_text(response.text);time.sleep(0.3)
   j=json.loads(p.read_text());d=pd.DataFrame(j['hourly']);d=features(d);d['weather_key']=key;d.to_parquet(out/f'{key}.parquet',index=False)
   row.update(status='downloaded',sha256=hashlib.sha256(p.read_bytes()).hexdigest(),rows=len(d),latitude=j['latitude'],longitude=j['longitude'],elevation_m=j['elevation'],units=j['hourly_units']);print(key,len(d),flush=True)
  except Exception as e:row.update(status='failed',error=str(e));print(key,str(e),flush=True)
  rows.append(row)
  (ROOT/'data/weather_manifest.json').write_text(json.dumps(rows,indent=2))
 (ROOT/'data/weather_manifest.json').write_text(json.dumps(rows,indent=2));(ROOT/'data/thresholds.json').write_text(json.dumps(THRESHOLDS,indent=2))
