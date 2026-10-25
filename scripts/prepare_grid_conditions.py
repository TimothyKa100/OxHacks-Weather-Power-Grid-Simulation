"""Point-only weather associations and every locally available SSEN outage report."""
from pathlib import Path
import json
import re
import sqlite3
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.neighbors import BallTree

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from failure_model.common import now,write_json
BASE=ROOT/'data/grid'


def nrn_parts(value):
    value=str(value).strip()
    if re.fullmatch(r'\d+\.\d+\.\d+\.\d+',value):
        a,b,*_=value.split('.');return f'{int(a)}:{int(b)}'
    if re.fullmatch(r'\d+_\d+',value):
        a,b=value.split('_');return f'{int(a)}:{int(b)}'
    if re.fullmatch(r'\d{7}',value):return f'{int(value[:4])}:{int(value[4:])}'
    return None


def build_node_weather(db,maximum_distance_km=35):
    metadata=[r for r in json.loads((ROOT/'data/weather_manifest.json').read_text())
              if r.get('status')=='downloaded' and (ROOT/'data/processed/weather'/f"{r['weather_key']}.parquet").exists()]
    cells=pd.DataFrame(metadata).drop_duplicates(['latitude','longitude']).reset_index(drop=True)
    tree=BallTree(np.deg2rad(cells[['latitude','longitude']].to_numpy()),metric='haversine')
    schema=pa.schema([(c,pa.string() if c in ['node_id','weather_key','nearest_weather_key','association_status'] else pa.float64()) for c in
                      ['node_id','longitude','latitude','weather_key','nearest_weather_key','provider_longitude','provider_latitude','distance_km','association_status']])
    db.execute('DROP TABLE IF EXISTS node_weather_links')
    counts={}
    path=BASE/'normalized/node_weather_links.parquet'
    with pq.ParquetWriter(path,schema,compression='zstd') as writer:
        cursor=db.execute('SELECT node_id,longitude,latitude FROM nodes ORDER BY rowid')
        while True:
            rows=cursor.fetchmany(50000)
            if not rows:break
            d=pd.DataFrame(rows,columns=['node_id','longitude','latitude'])
            valid=d.latitude.between(-90,90)&d.longitude.between(-180,180)
            for c in ['weather_key','nearest_weather_key']:d[c]=None
            for c in ['provider_longitude','provider_latitude','distance_km']:d[c]=np.nan
            d['association_status']='unlocated_asset'
            if valid.any():
                distance,index=tree.query(np.deg2rad(d.loc[valid,['latitude','longitude']].to_numpy()),k=1)
                selected=cells.iloc[index[:,0]]
                km=distance[:,0]*6371.0088
                d.loc[valid,'nearest_weather_key']=selected.weather_key.to_numpy()
                d.loc[valid,'provider_longitude']=selected.longitude.to_numpy()
                d.loc[valid,'provider_latitude']=selected.latitude.to_numpy()
                d.loc[valid,'distance_km']=km
                d.loc[valid,'association_status']=np.where(km<=maximum_distance_km,'coarse_regional_reanalysis_at_node','no_downloaded_weather_within_35km')
                d.loc[valid,'weather_key']=np.where(km<=maximum_distance_km,selected.weather_key.to_numpy(),None)
            d.to_sql('node_weather_links',db,if_exists='append',index=False,chunksize=5000)
            writer.write_table(pa.Table.from_pandas(d,schema=schema,preserve_index=False))
            for key,value in d.association_status.value_counts().items():counts[key]=counts.get(key,0)+int(value)
    db.execute('CREATE UNIQUE INDEX node_weather_node_idx ON node_weather_links(node_id)')
    print('Node weather coverage',counts,flush=True)
    return counts


def all_outages(db):
    frames=[]
    for path in sorted((ROOT/'data/raw/nafirs-hv-faults').glob('*.csv')):
        try:d=pd.read_csv(path,dtype=str,keep_default_na=False,encoding='utf-8-sig')
        except UnicodeDecodeError:d=pd.read_csv(path,dtype=str,keep_default_na=False,encoding='cp1252')
        op='SHEPD' if 'NRN_NORTH' in d else 'SEPD'
        d['operator']=op;d['network_reference']=d['NRN_NORTH' if op=='SHEPD' else 'NRN_SOUTH'];d['source_resource']=path.stem
        d.columns=d.columns.str.lower();frames.append(d)
    d=pd.concat(frames,ignore_index=True).drop_duplicates().reset_index(drop=True)
    d['source_event_key']=d.operator+':'+d.reporting_year+':'+d.dist_hv_ref
    d['duplicate_source_key']=d.source_event_key.duplicated(keep=False)
    d['event_key']=d.source_event_key
    if d.duplicate_source_key.any():
        numbers=d.groupby('source_event_key').cumcount().astype(str)
        d.loc[d.duplicate_source_key,'event_key']=d.loc[d.duplicate_source_key,'source_event_key']+':record:'+numbers[d.duplicate_source_key]
    local=pd.to_datetime(d.hv_incident_time,dayfirst=True,errors='coerce')
    d['reported_at_local']=local.dt.strftime('%Y-%m-%d %H:%M:%S')
    actual=local.dt.tz_localize('Europe/London',ambiguous='NaT',nonexistent='NaT').dt.tz_convert('UTC')
    d['reported_at_utc']=actual.map(lambda t:t.isoformat() if pd.notna(t) else None)
    d['weather_hour_utc']=actual.dt.floor('h')
    d['timezone_status']='provisional_Europe_London_not_confirmed'
    d['nrn_key']=d.network_reference.map(nrn_parts)
    roster=pd.read_parquet(ROOT/'data/processed/circuit_roster.parquet')
    roster_map=(roster.operator+':'+roster.nrn_candidate_key)
    lookup=pd.Series(roster.circuit_key.to_numpy(),index=roster_map)
    d['location_asset_id']=(d.operator+':'+d.nrn_key).map(lookup)
    d['location_basis']=np.where(d.location_asset_id.notna(),'candidate_feeder_representative_point','unlocated_report')
    circuits=pd.read_parquet(ROOT/'data/processed/circuits.parquet')
    circuits['key']=circuits.operator_candidate+':'+circuits.nrn_candidate_key
    unique=circuits[circuits.key.map(circuits.key.value_counts()).eq(1)].set_index('key').circuit_key
    fallback=(d.operator+':'+d.nrn_key).map(unique)
    use=d.location_asset_id.isna()&fallback.notna()
    d.loc[use,'location_asset_id']=fallback[use]
    d.loc[use,'location_basis']='candidate_GIS_circuit_representative_point'
    nodes=pd.read_sql_query("SELECT node_id,longitude,latitude FROM nodes WHERE node_type IN ('feeder','circuit')",db).set_index('node_id')
    d['longitude']=d.location_asset_id.map(nodes.longitude);d['latitude']=d.location_asset_id.map(nodes.latitude)
    d.loc[d.latitude.isna(),'location_basis']='unlocated_report'
    d['place']=d.primary_name.where(d.primary_name.ne(''),d.district_short_code)
    possible=set(pd.read_parquet(ROOT/'data/processed/excluded_incident_audit.parquet').event_key)
    d['report_class']=np.where(d.source_event_key.isin(possible),'possible_operational_or_planned','unverified_incident')
    d['planned_status']='unknown_not_published'
    d['training_eligible']=False;d['asset_failure_verified']=False;d['failed_asset_id']=None
    d['flood_depth_m']=np.nan;d['flood_status']='no_verified_inundation_data'
    d['restored_at_utc']=None
    for source,target in [('hv_cust_aff','customers_affected'),('damage','damage'),('voltage_1','reported_voltage_kv'),('avg_time_off_mins','average_customer_minutes_off')]:
        d[target]=pd.to_numeric(d[source],errors='coerce')
    links=pd.read_sql_query("SELECT node_id,weather_key,provider_longitude,provider_latitude,distance_km,association_status FROM node_weather_links WHERE node_id IN (SELECT node_id FROM nodes WHERE node_type IN ('feeder','circuit'))",db).set_index('node_id')
    d['weather_key']=d.location_asset_id.map(links.weather_key)
    d['weather_distance_km']=d.location_asset_id.map(links.distance_km)
    fields=['wind_speed_10m','wind_gusts_10m','wind_direction_10m','temperature_2m','precipitation']
    for field in fields:d[field]=np.nan
    for key, group in d[d.weather_key.notna()&d.weather_hour_utc.notna()].groupby('weather_key'):
        weather=pd.read_parquet(ROOT/'data/processed/weather'/f'{key}.parquet',columns=['time',*fields]).set_index('time')
        sampled=weather.reindex(group.weather_hour_utc)
        d.loc[group.index,fields]=sampled[fields].to_numpy()
    d['weather_hour_utc']=d.weather_hour_utc.map(lambda t:t.isoformat() if pd.notna(t) else None)
    d['conditions_status']=np.where(d.wind_gusts_10m.notna(),'historical_grid_estimate_at_candidate_location','conditions_unavailable')
    keep=['event_key','source_event_key','operator','reported_at_local','reported_at_utc','timezone_status','place','district_short_code',
          'network_reference','location_asset_id','location_basis','longitude','latitude','cause','contributory_cause','equipment','component',
          'damage','customers_affected','reported_voltage_kv','average_customer_minutes_off','report_class','planned_status',
          'training_eligible','asset_failure_verified','failed_asset_id','restored_at_utc','weather_key','weather_hour_utc','weather_distance_km',
          *fields,'conditions_status','flood_depth_m','flood_status','source_resource','duplicate_source_key']
    result=d[keep]
    result.to_parquet(BASE/'normalized/all_outage_reports.parquet',index=False,compression='zstd')
    result.to_sql('outages',db,if_exists='replace',index=False,chunksize=2000)
    db.execute('CREATE UNIQUE INDEX outage_event_idx ON outages(event_key)')
    db.execute('CREATE INDEX outage_time_operator_idx ON outages(reported_at_local,operator)')
    db.execute('CREATE INDEX outage_location_idx ON outages(location_asset_id)')
    report={'total':len(result),'mapped':int(result.latitude.notna().sum()),'unmapped':int(result.latitude.isna().sum()),
            'conditions_available':int(result.wind_gusts_10m.notna().sum()),'unresolved_utc':int(result.reported_at_utc.isna().sum()),
            'earliest':str(result.reported_at_local.min()),'latest':str(result.reported_at_local.max()),
            'possible_operational':int(result.report_class.eq('possible_operational_or_planned').sum()),
            'verified_asset_failures':0,'training_eligible':0,
            'scope':'All locally downloaded SSEN HV reports; not every outage in UK history',
            'location_precision':'Feeder/circuit representative points; actual failed-component locations unverified'}
    db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('outages',json.dumps(report)))
    return report


def export_nodes(db):
    fields=['node_id','node_type','asset_category','node_scope','source','name','operator','longitude','latitude','location_method',
            'voltage_kv','r_ohm','x_ohm','capacity_mva','generation_capacity_mw','storage_capacity_mwh','length_km','terrain_elevation_m','lifecycle_status','source_ref']
    extra=['min_zoom','structure_height_m','structure_height_min_m','structure_height_max_m','height_basis','asset_top_elevation_m']
    available={r[1] for r in db.execute('PRAGMA table_info(nodes)')}
    fields += [c for c in extra if c in available]
    numeric={'longitude','latitude','voltage_kv','r_ohm','x_ohm','capacity_mva','generation_capacity_mw','storage_capacity_mwh','length_km','terrain_elevation_m',
             'min_zoom','structure_height_m','structure_height_min_m','structure_height_max_m','asset_top_elevation_m'}
    schema=pa.schema([(c,pa.float64() if c in numeric else pa.string()) for c in fields])
    with pq.ParquetWriter(BASE/'normalized/clean_grid_node_locations.parquet',schema,compression='zstd') as writer:
        for chunk in pd.read_sql_query('SELECT '+','.join(fields)+' FROM nodes',db,chunksize=50000):
            writer.write_table(pa.Table.from_pandas(chunk,schema=schema,preserve_index=False))


def sampling_plan(db):
    plan=pd.read_sql_query('''SELECT ROUND(latitude*4)/4 latitude,ROUND(longitude*4)/4 longitude,COUNT(*) node_records
        FROM nodes WHERE latitude IS NOT NULL AND longitude IS NOT NULL GROUP BY 1,2''',db)
    manifest=[r for r in json.loads((ROOT/'data/weather_manifest.json').read_text()) if r.get('status')=='downloaded']
    available={(float(r['latitude']),float(r['longitude'])) for r in manifest}
    plan['weather_key']=[f'{lat+0.0:.2f}_{lon+0.0:.2f}' for lat,lon in zip(plan.latitude,plan.longitude)]
    plan['matching_native_cell_cached']=[(lat,lon) in available for lat,lon in zip(plan.latitude,plan.longitude)]
    plan['scope']='Node locations only; 0.25-degree ERA5 request plan, not observations'
    plan.to_parquet(BASE/'normalized/node_weather_sampling_plan.parquet',index=False)
    return {'locations':len(plan),'matching_native_cells_cached':int(plan.matching_native_cell_cached.sum())}


def main():
    path=BASE/'uk_power_grid.sqlite'
    if not path.exists():raise SystemExit('Build the cleaned grid with scripts/clean_power_grid.py first')
    with sqlite3.connect(f'{path.resolve().as_uri()}?mode=rw',uri=True) as db:
        weather=build_node_weather(db)
        outages=all_outages(db)
        export_nodes(db)
        plan=sampling_plan(db)
        report={'generated_utc':now(),'node_weather':weather,'maximum_weather_distance_km':35,'weather_geometry':'node point only',
                'weather_resolution_degrees':.25,'outages':outages,'flood_depth_available':False,
                'national_node_sampling_plan':plan,
                'causality_note':'Reported causes and coarse weather associations do not verify physical breakage or establish causality.'}
        db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('node_conditions',json.dumps(report)))
        db.commit()
    write_json(BASE/'reports/node_conditions.json',report)
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
