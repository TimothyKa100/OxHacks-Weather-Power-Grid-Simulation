"""Normalise archived public snapshots and create uncertain postcode/route joins.

One incident may affect many postcodes. Postcode rows do not multiply incident
counts, customer totals are never summed across repeated postcode rows, and ETR
is never used as actual restoration. Only UK land locations enter the catalogue.
"""
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3
import sys

import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Point
from shapely.ops import transform
from pyproj import Transformer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.refine_uk_grid import land_boundary,on_land
from failure_model.common import now,write_json

BASE=ROOT/'data/grid'; RAW=BASE/'raw/outages'
PC=re.compile(r'[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}')


def postcodes(value):
    return sorted(set(re.sub(r'\s+','',x) for x in PC.findall(str(value or '').upper())))


def timestamp(value):
    if not value or pd.isna(value): return None,None,'missing_source_time'
    t=pd.Timestamp(value)
    if t.tzinfo is None:
        utc=t.tz_localize('Europe/London',ambiguous='NaT',nonexistent='NaT')
        status='provisional_Europe_London_not_confirmed'
    else: utc=t;status='explicit_source_UTC_offset'
    return t.tz_localize(None).isoformat(' ') if t.tzinfo is None else t.tz_convert('Europe/London').tz_localize(None).isoformat(' '), utc.tz_convert('UTC').isoformat() if pd.notna(utc) else None,status


def record(operator,ident,start,raw,**kwargs):
    local,utc,status=timestamp(start)
    return dict(event_key=f'{operator}:LIVE:{ident}:{(local or "unknown")[:10]}',source_event_key=str(ident),operator=operator,
                reported_at_local=local,reported_at_utc=utc,timezone_status=status,
                report_class='unverified_incident',planned_status='unknown_not_published',
                training_eligible=False,asset_failure_verified=False,failed_asset_id=None,
                location_asset_id=None,location_basis='published_affected_area_point',
                source_properties_json=json.dumps(raw,default=str),source_kind='live_snapshot',
                location_precision='affected_area_not_failure_location', **kwargs)


def source_rows():
    for path in sorted((RAW/'ukpn-live-faults').glob('*.json')):
        for r in json.loads(path.read_text()):
            point=r.get('geopoint') or {}
            yield record('UKPN',r['incidentreference'],r.get('creationdatetime'),r,
                         longitude=point.get('lon'),latitude=point.get('lat'),place=r.get('operatingzone'),
                         cause=r.get('incidentcategorycustomerfriendlydescription'),customers_affected=r.get('nocustomeraffected'),
                         postcodes_json=json.dumps(postcodes(r.get('fullpostcodedata'))),
                         source_resource=str(path.relative_to(ROOT)),
                         restored_at_utc=timestamp(r.get('restoreddatetime'))[1],
                         estimated_restoration_at_utc=timestamp(r.get('estimatedrestorationdate'))[1],
                         published_planned=r.get('powercuttype')=='Planned')
    for path in sorted((RAW/'live-power-cuts-data').glob('*.json')):
        groups={}
        for r in json.loads(path.read_text()):groups.setdefault(r['reference'],[]).append(r)
        for ident,rows in groups.items():
            r=max(rows,key=lambda x:x.get('updatedate') or '')
            yield record('NPG',ident,r.get('loggedtime'),rows,
                         longitude=r.get('lng'),latitude=r.get('lat'),place=r.get('area'),cause=r.get('reason'),
                         customers_affected=max((x.get('totalconfirmedpowercut') or 0) for x in rows),
                         postcodes_json=json.dumps(postcodes([x.get('postcode') for x in rows])),
                         source_resource=str(path.relative_to(ROOT)),
                         estimated_restoration_at_utc=timestamp(r.get('estimatedtimetillresolution'))[1],
                         published_planned='planned' in str(r.get('natureofoutage','')).lower())
    # Merge summary and detailed snapshots by source ID, with detailed attributes preferred.
    nged={}
    mapping={'Incident ID':'fault_id','Start Time':'date_of_reported_fault','Location Longitude':'location_longitude',
             'Location Latitude':'location_latitude','Postcodes':'postcode','Region':'licence_area','Confirmed Off':'confirmed_off',
             'Planned':'planned','Category':'category','ETR':'etr'}
    paths=list(sorted(RAW.glob('nged_292*/*.csv')))+list(sorted(RAW.glob('nged_a136*/*.csv')))
    for path in paths:
        for r in pd.read_csv(path).rename(columns=mapping).astype(object).replace({np.nan:None}).to_dict('records'):
            nged.setdefault(r['fault_id'],{}).update({k:v for k,v in r.items() if v is not None})
    for ident,r in nged.items():
        planned=str(r.get('planned')).lower()=='true'
        yield record('NGED',ident,r.get('date_of_reported_fault') or r.get('planned_outage_start_date'),r,
                     longitude=r.get('location_longitude'),latitude=r.get('location_latitude'),
                     place=r.get('licence_area'),cause=r.get('category'),customers_affected=r.get('confirmed_off'),
                     postcodes_json=json.dumps(postcodes(r.get('postcode'))),source_resource='NGED live-power-cuts summary + detailed archive',
                     restored_at_utc=timestamp(r.get('date_of_restoration'))[1],estimated_restoration_at_utc=timestamp(r.get('etr'))[1],published_planned=planned)
    for path in sorted((RAW/'ssen_live').glob('*.json')):
        for r in json.loads(path.read_text()).get('faults',[]):
            p=r.get('location') or {}
            yield record('SSEN_LIVE',r['reference'],r.get('loggedAtUtc'),r,
                         longitude=p.get('longitude'),latitude=p.get('latitude'),place=r.get('title'),cause=r.get('message') or r.get('type'),
                         customers_affected=r.get('customerCount'),postcodes_json=json.dumps(postcodes(r.get('affectedAreas'))),
                         network_reference=f'{r.get("primaryNrn")}:{r.get("hvFeederNrn")}',
                         source_resource=str(path.relative_to(ROOT)),estimated_restoration_at_utc=timestamp(r.get('estimatedRestorationTimeUtc'))[1],
                         published_planned=True if r.get('type')=='PSI' else False if r.get('type') in ('HV','LV') else None)


def locate(records,land,cache):
    accepted=[];excluded=[]
    for r in records:
        pcs=json.loads(r['postcodes_json']);points=[cache.get(p) for p in pcs];points=[p for p in points if p and p.get('longitude') is not None]
        lon,lat=r.get('longitude'),r.get('latitude')
        if lon is None or lat is None:
            if points:
                lon=float(np.mean([p['longitude'] for p in points]));lat=float(np.mean([p['latitude'] for p in points]))
                # A centroid of coastal postcodes can be in the sea. Choose an observed land postcode instead.
                if not on_land(land,[lon],[lat])[0]:
                    usable=[p for p in points if on_land(land,[p['longitude']],[p['latitude']])[0]]
                    if usable:lon,lat=usable[0]['longitude'],usable[0]['latitude']
                r['location_basis']='affected_postcode_centroid_or_representative'
                r['longitude'],r['latitude']=lon,lat
        if lon is None or lat is None or not on_land(land,[lon],[lat])[0]:excluded.append(r);continue
        planned=r.pop('published_planned')
        if planned is not None:
            r['planned_status']='published_planned' if planned else 'published_unplanned'
            if planned:r['report_class']='possible_operational_or_planned'
        accepted.append(r)
    return accepted,excluded


def circuit_candidates(db,events,cache):
    """Candidate route portions within 1 km of postcode centres, within operator.

    These distances/ranks are not probabilities or evidence that the nearest
    circuit supplied that postcode. Full affected supply polygons are preferable.
    """
    forward=Transformer.from_crs(4326,27700,always_xy=True).transform
    backward=Transformer.from_crs(27700,4326,always_xy=True).transform
    rows=[]
    # Published SSEN NRN gives a feeder-level candidate without spatial guessing.
    for event in events:
        if event['operator']=='SSEN_LIVE':
            ref=event.get('network_reference','')
            matches=db.execute('SELECT node_id FROM nodes WHERE node_id IN (?,?)',tuple(f'{region}:FEEDER:{ref}' for region in ['SEPD','SHEPD'])).fetchall()
            if len(matches)==1:
                event['location_asset_id']=matches[0][0];event['location_precision']='published_feeder_reference_not_failed_component'
                rows.append(dict(event_key=event['event_key'],node_id=matches[0][0],postcode=None,distance_m=None,
                                 route_fraction=None,segment_longitude=None,segment_latitude=None,
                                 method='published_primary_and_HV_feeder_NRN',confidence='feeder_reference_only',rank=1))
    # NGED and SSEN have downloaded operator-specific route geometry. UKPN/NPG
    # do not currently have a matching route inventory: leave them unresolved.
    for operator,pattern in [('NGED','NGED:%'),('SSEN_LIVE','SSEN_GIS')]:
        routes=pd.read_sql_query("SELECT node_id,geometry_wgs84_wkb FROM nodes WHERE source LIKE ? AND node_type='power_line' AND geometry_wgs84_wkb IS NOT NULL",db,params=[pattern])
        if routes.empty:continue
        geoms=[transform(forward,shapely.from_wkb(v)) for v in routes.geometry_wgs84_wkb]
        tree=shapely.STRtree(geoms)
        for event in events:
            if event['operator']!=operator:continue
            for postcode in json.loads(event['postcodes_json']):
                p=cache.get(postcode)
                if not p or p.get('longitude') is None:continue
                point=transform(forward,Point(p['longitude'],p['latitude']))
                candidates=tree.query(point,predicate='dwithin',distance=1000)
                ordered=sorted(candidates,key=lambda i:geoms[i].distance(point))[:5]
                for rank,i in enumerate(ordered,1):
                    g=geoms[i]
                    if g.geom_type not in ('LineString','MultiLineString'):continue
                    fraction=float(g.project(point,normalized=True));nearest=transform(backward,g.interpolate(fraction,normalized=True))
                    rows.append(dict(event_key=event['event_key'],node_id=routes.node_id.iloc[i],postcode=postcode,
                                     distance_m=float(g.distance(point)),route_fraction=fraction,
                                     segment_longitude=nearest.x,segment_latitude=nearest.y,
                                     method='postcode_centroid_near_operator_route_1km',confidence='low_spatial_candidate_only',rank=rank))
    columns=['event_key','node_id','postcode','distance_m','route_fraction','segment_longitude','segment_latitude','method','confidence','rank']
    frame=pd.DataFrame(rows,columns=columns)
    frame.to_parquet(BASE/'normalized/outage_circuit_candidates.parquet',index=False)
    frame.to_sql('outage_circuit_candidates',db,if_exists='replace',index=False)
    db.execute('CREATE INDEX outage_candidate_event ON outage_circuit_candidates(event_key)')
    return frame


def main():
    cache=json.loads((RAW/'postcode_coordinates.json').read_text()) if (RAW/'postcode_coordinates.json').exists() else {}
    deduplicated={r['event_key']:r for r in source_rows()}
    events,excluded=locate(deduplicated.values(),land_boundary(),cache)
    with sqlite3.connect(BASE/'uk_power_grid.sqlite') as db:
        candidates=circuit_candidates(db,events,cache)
        existing={r[1] for r in db.execute('PRAGMA table_info(outages)')}
        additions={'postcodes_json':'TEXT','source_properties_json':'TEXT','source_kind':'TEXT','location_precision':'TEXT','estimated_restoration_at_utc':'TEXT'}
        for col,typ in additions.items():
            if col not in existing:db.execute(f'ALTER TABLE outages ADD COLUMN {col} {typ}')
        columns=[r[1] for r in db.execute('PRAGMA table_info(outages)')]
        for event in events:
            # Incremental/upsert: repeated snapshots update an incident, never duplicate it.
            values={k:v for k,v in event.items() if k in columns}
            db.execute('INSERT OR REPLACE INTO outages ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',list(values.values()))
        db.commit()
        pd.read_sql_query('SELECT * FROM outages',db).to_parquet(BASE/'normalized/all_outage_reports.parquet',index=False)
        stats=dict(db.execute('SELECT operator,COUNT(*) FROM outages GROUP BY operator'))
    pd.DataFrame(excluded).to_parquet(BASE/'normalized/excluded_live_outages.parquet',index=False)
    report=dict(generated_utc=now(),active_live_reports=len(events),excluded_missing_or_non_UK_land=len(excluded),
                by_operator=stats,candidate_rows=len(candidates),events_with_candidates=int(candidates.event_key.nunique()),
                candidate_methods=candidates.method.value_counts().to_dict(),verified_failed_assets=0,
                note='Affected postcode centres and published feeder references support uncertain associations only; no new physical-failure training labels.')
    write_json(BASE/'reports/uk_outages.json',report);print(json.dumps(report,indent=2))


if __name__=='__main__':main()
