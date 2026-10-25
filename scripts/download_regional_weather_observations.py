"""Observed hourly weather for regional storm validation, retaining NOAA quality flags."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path

import pandas as pd
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/grid/raw/research/weather_observations'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    url='https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv'
    response=requests.get(url,timeout=60);response.raise_for_status();(OUT/'isd-history.csv').write_bytes(response.content)
    stations=pd.read_csv(io.StringIO(response.text),dtype=str)
    selected=stations[stations.ICAO.isin(['EGAA','EGAC','EGPH','EGPK','EGNT','EGNM']) |
                      ((stations.CTRY.isin(['UK','GB'])) & stations['STATION NAME'].str.contains('ESKDALEMUIR|SHAP|LEEMING',na=False))]
    selected.to_csv(OUT/'selected_stations.csv',index=False)
    jobs=[(row,year) for row in selected.to_dict('records') for year in [2021,2025]
          if row['BEGIN']<=f'{year}1231' and row['END']>=f'{year}0101']
    def fetch(job):
        row,year=job;ident=row['USAF']+row['WBAN'];url=f'https://noaa-global-hourly-pds.s3.amazonaws.com/{year}/{ident}.csv'
        path=OUT/f'{year}_{ident}.csv';record=dict(url=url,path=str(path.relative_to(ROOT)),station=ident,year=year,station_name=row['STATION NAME'])
        try:
            if not path.exists():
                response=requests.get(url,timeout=120);response.raise_for_status()
                # CSV headers may be quoted. Validate the parsed schema instead of raw prefixes.
                frame=pd.read_csv(io.BytesIO(response.content),nrows=1)
                if not {'STATION','DATE','WND'}.issubset(frame.columns):raise ValueError('Unexpected weather schema')
                path.write_bytes(response.content)
            data=path.read_bytes();record.update(status='downloaded',bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        except (requests.RequestException,ValueError) as error:record.update(status='unavailable',error=str(error))
        return record
    with ThreadPoolExecutor(3) as pool:files=list(pool.map(fetch,jobs))
    (OUT/'manifest.json').write_text(json.dumps(dict(source='NOAA NCEI Global Hourly / ISD',
        scope='NI, southern Scotland and northern England station observations for 2021/2025; ISD ends 2025-08-24; annual file may have missing periods',
        service_notice='https://www.nesdis.noaa.gov/news/service-location-change-integrated-surface-data-global-hourly',
        interpretation='Station observations for validating reanalysis/storm severity; retain quality codes and source units; not asset measurements',files=files),indent=2))
    print('Downloaded',sum(x['status']=='downloaded' for x in files),'of',len(files),'station-years',flush=True)


if __name__=='__main__':main()
