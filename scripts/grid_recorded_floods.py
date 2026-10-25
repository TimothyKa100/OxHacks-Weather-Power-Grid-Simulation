"""Associate node points with dated EA recorded flood footprints (England only)."""
from pathlib import Path
import hashlib
import json
import sqlite3
import sys
import zipfile

import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from failure_model.common import now,write_json
BASE=ROOT/'data/grid'
URL='https://environment.data.gov.uk/file-management-open/data-sets/ed73f2e8-a3c2-44db-952d-6e359c7c3987/files/Recorded_Flood_Outlines.gpkg.zip'


def export_outage_flood_evidence(db):
    db.execute('UPDATE outages SET recorded_flood_outline_matches=NULL WHERE location_asset_id IS NULL OR reported_at_local IS NULL')
    records=pd.read_sql_query('SELECT * FROM outages',db)
    for column in ['training_eligible','asset_failure_verified','duplicate_source_key']:
        records[column]=records[column].astype(bool)
    records.to_parquet(BASE/'normalized/all_outage_reports.parquet',index=False,compression='zstd')


def gpkg_wkb(blob):
    if blob[:2]!=b'GP':raise ValueError('Expected GeoPackage geometry')
    envelope=(blob[3]>>1)&7
    sizes={0:0,1:32,2:48,3:48,4:64}
    if envelope not in sizes:raise ValueError('Unsupported GeoPackage envelope')
    return blob[8+sizes[envelope]:]


def main():
    raw=BASE/'raw/floods';raw.mkdir(parents=True,exist_ok=True)
    archive=raw/'Recorded_Flood_Outlines.gpkg.zip';path=raw/'Recorded_Flood_Outlines.gpkg'
    if not archive.exists():
        import requests
        response=requests.get(URL,stream=True,timeout=(15,120));response.raise_for_status()
        with archive.open('wb') as stream:
            for chunk in response.iter_content(1024*1024):stream.write(chunk)
    if not path.exists():
        with zipfile.ZipFile(archive) as package,package.open('Recorded_Flood_Outlines.gpkg') as source,path.open('wb') as target:
            import shutil
            shutil.copyfileobj(source,target)
    # GDAL linearizes the publisher's curved GeoPackage geometries before GEOS
    # operations; raw WKB readers cannot handle all of these source geometries.
    from pyogrio.raw import read
    meta,ids,wkb,values=read(path,return_fids=True,datetime_as_string=True)
    from pyproj import CRS
    if CRS(meta['crs']).to_epsg()!=27700:raise ValueError('Inspect flood CRS before using this adapter')
    properties=pd.DataFrame(dict(zip(meta['fields'],values)))
    geometries=shapely.make_valid(shapely.from_wkb(wkb))
    records=properties[['rec_out_id','name','start_date','end_date','flood_src','flood_caus','data_qual']].rename(columns={
        'rec_out_id':'publisher_reference','name':'event_name','start_date':'start_date_raw','end_date':'end_date_raw',
        'flood_src':'flood_source','flood_caus':'reported_flood_cause','data_qual':'source_quality'})
    records.insert(0,'outline_id',['EA:RFO:'+str(i) for i in ids])
    records['geometry_bng_wkb']=shapely.to_wkb(geometries)
    for source,target in [('start_date_raw','start_date'),('end_date_raw','end_date')]:
        records[target]=pd.to_datetime(records[source],errors='coerce',utc=True).dt.strftime('%Y-%m-%d')
    records['date_precision']='source_date_range_precision_unverified'
    records.to_parquet(BASE/'normalized/recorded_flood_outlines.parquet',index=False,compression='zstd')
    tree=shapely.STRtree(geometries);shapely.prepare(geometries)
    transform=Transformer.from_crs(4326,27700,always_xy=True)
    count=0
    with sqlite3.connect(f'{(BASE/"uk_power_grid.sqlite").resolve().as_uri()}?mode=rw',uri=True) as db:
        records.drop(columns='geometry_bng_wkb').to_sql('flood_outlines',db,index=False,if_exists='replace')
        db.execute('DROP TABLE IF EXISTS node_flood_history')
        db.execute('CREATE TABLE node_flood_history(node_id TEXT,outline_id TEXT)')
        cursor=db.execute('SELECT node_id,longitude,latitude FROM nodes WHERE latitude IS NOT NULL AND longitude IS NOT NULL')
        while True:
            batch=cursor.fetchmany(50000)
            if not batch:break
            ids=np.array([r[0] for r in batch],dtype=object)
            lon=np.array([r[1] for r in batch]);lat=np.array([r[2] for r in batch])
            x,y=transform.transform(lon,lat)
            points=shapely.points(x,y)
            pairs=tree.query(points)
            if pairs.shape[1]:
                valid=shapely.covers(geometries[pairs[1]],points[pairs[0]])
                pairnodes=ids[pairs[0,valid]];pairoutlines=records.outline_id.to_numpy()[pairs[1,valid]]
                db.executemany('INSERT INTO node_flood_history VALUES (?,?)',zip(pairnodes,pairoutlines))
                count+=len(pairnodes)
        db.execute('CREATE INDEX node_flood_node_idx ON node_flood_history(node_id)')
        db.execute('CREATE UNIQUE INDEX flood_outline_idx ON flood_outlines(outline_id)')
        affected=db.execute('SELECT COUNT(DISTINCT node_id) FROM node_flood_history').fetchone()[0]
        pd.read_sql_query('SELECT * FROM node_flood_history',db).to_parquet(BASE/'normalized/node_recorded_flood_history.parquet',index=False,compression='zstd')
        # Match source date ranges only; do not infer exact inundation time or depth.
        db.execute('ALTER TABLE outages ADD COLUMN recorded_flood_outline_matches INTEGER') if 'recorded_flood_outline_matches' not in [r[1] for r in db.execute('PRAGMA table_info(outages)')] else None
        db.execute('''UPDATE outages SET recorded_flood_outline_matches=(SELECT COUNT(*) FROM node_flood_history h
            JOIN flood_outlines f ON f.outline_id=h.outline_id WHERE h.node_id=outages.location_asset_id
            AND f.start_date<=substr(outages.reported_at_local,1,10)
            AND COALESCE(f.end_date,f.start_date)>=substr(outages.reported_at_local,1,10))''')
        matches=db.execute('SELECT COUNT(*) FROM outages WHERE recorded_flood_outline_matches>0').fetchone()[0]
        export_outage_flood_evidence(db)
        report={'generated_utc':now(),'source':URL,'source_page':'https://environment.data.gov.uk/dataset/8c75e700-d465-11e4-8b5b-f0def148f590',
                'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'license':'Open Government Licence v3.0',
                'attribution':'© Environment Agency copyright and/or database right 2025. All rights reserved.',
                'outline_records':len(records),'node_outline_links':count,'nodes_in_recorded_footprints':affected,
                'outage_proxy_locations_with_matching_source_dates':matches,'coverage':'England only; archive is incomplete',
                'geometry':'node points, not routes','flood_depth_available':False,
                'meaning':'Published outline and source-date-range overlap. Not proof of internal asset inundation, breakage or exact flood timing.',
                'negative_evidence':'No matching record means unknown, not no flooding.'}
        db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('recorded_floods',json.dumps(report)))
        db.commit()
    write_json(BASE/'reports/recorded_floods.json',report)
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
