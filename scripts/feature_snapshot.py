"""Assemble a reviewable feeder feature snapshot, without inventing targets or at-risk status."""
from pathlib import Path
import argparse
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/processed'
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--at',default='2021-11-26T12:00:00Z');args=ap.parse_args();at=pd.Timestamp(args.at)
 if at.tzinfo is None:raise ValueError('Use an explicit UTC timestamp')
 at=at.tz_convert('UTC')
 c=pd.read_parquet(P/'circuit_roster.parquet').drop(columns=['geometry_wgs84_wkb']);cross=pd.read_parquet(P/'roster_weather_crosswalk.parquet')
 joined=c.merge(cross[['circuit_key','weather_key','weather_available','distance_to_request_point_km','weather_grid_elevation_m']],on='circuit_key',validate='one_to_one');rows=[]
 for key in cross.loc[cross.weather_available,'weather_key'].unique():
  path=P/'weather'/f'{key}.parquet';d=pd.read_parquet(path,filters=[('time','==',at)])
  if len(d)>1:raise ValueError('Duplicate weather key and timestamp')
  cols=['weather_key']+[col for col in d if '_prev_' in col]
  if len(d):rows.append(d[cols])
 if rows:joined=joined.merge(pd.concat(rows,ignore_index=True),on='weather_key',how='left',validate='many_to_one')
 joined['prediction_time_utc']=at;joined['at_risk_verified']=False;joined['target_available']=False;joined['training_ready']=False
 out=P/'feature_snapshot.parquet';joined.to_parquet(out,index=False);print(len(joined),'feeder rows:',out)
if __name__=='__main__':main()
