"""Import supplied outage episodes and official NIE snapshots with source evidence.

Never infer actual restoration from an archive's last sighting. Preserve reused
incident IDs as separate episodes, and record aliases when an existing source
report overlaps. No spatial association creates verified physical-failure labels.
"""
from collections import Counter,defaultdict
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pandas as pd
from pyproj import Transformer
import shapely
from shapely.geometry import shape

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.refine_uk_grid import land_boundary,on_land
from scripts.build_uk_outages import postcodes
from failure_model.common import now,write_json

BASE=ROOT/'data/grid'
SOURCE=BASE/'raw/outages/user_supplied/unplanned_outages.csv'
ALIASES={'NPg':'NPG','WPD':'NGED','SSEN_LIVE':'SSEN','SEPD':'SSEN','SHEPD':'SSEN'}


def canonical_operator(value):return ALIASES.get(value,value)


def utc(value):
    if value is None or pd.isna(value) or value=='':return None
    try:
        t=pd.Timestamp(value)
        if pd.isna(t):return None
        return (t.tz_localize('UTC') if t.tzinfo is None else t.tz_convert('UTC')).isoformat()
    except (ValueError,TypeError):return None


def normalise_archive(row):
    r={k:None if pd.isna(v) else v for k,v in row.items()}
    start=utc(r['start_time_utc']);basis=r['start_time_basis'];end_basis=r['end_time_basis']
    restored=utc(r['end_time_utc']) if end_basis=='reported_restored' else None
    flags=[]
    if basis!='reported':flags.append('onset_not_reported')
    if end_basis!='reported_restored':flags.append('actual_restoration_unknown')
    if restored and start and restored<start:
        flags.append('restoration_before_display_time');restored=None
    return dict(event_key='ARCHIVE:'+str(r['record_id']),source_event_key=str(r['incident_ref']),
                source_record_id=r['record_id'],operator=canonical_operator(r['operator']),
                reported_at_utc=start,reported_at_local=pd.Timestamp(start).tz_convert('Europe/London').tz_localize(None).isoformat(' ') if start else None,
                timezone_status='explicit_UTC_as_supplied_unverified_lineage',
                latitude=r['lat'],longitude=r['lon'],place=r['region'],cause=r['cause'] or r['category'],
                equipment=None,component=None,customers_affected=r['customers_affected'],
                postcodes_json=json.dumps(postcodes(r['postcodes'])),
                report_class='unverified_incident',planned_status='unplanned_as_archive_classified',
                location_asset_id=None,location_basis=r['location_method'],location_precision='affected_area_archive_point',
                restored_at_utc=restored,estimated_restoration_at_utc=utc(r['estimated_restoration_utc']),
                source_kind='user_supplied_archive',source_resource=str(SOURCE.relative_to(ROOT)),
                source_properties_json=json.dumps(r,allow_nan=False),
                archive_source=r['source'],archive_start_basis=basis,archive_end_basis=end_basis,
                first_seen_utc=utc(r['first_seen_utc']),last_seen_utc=utc(r['last_seen_utc']),
                start_time_earliest_utc=utc(r['start_time_earliest_utc']),reported_start_as_supplied_utc=utc(r['reported_start_utc']),
                end_time_as_supplied_utc=utc(r['end_time_utc']),archive_duration_minutes=r['duration_min'],
                evidence_flags=json.dumps(flags),provenance_status='user_supplied_upstream_lineage_unverified',
                calibration_role='regional_positive_observation_or_validation_only',
                training_eligible=False,asset_failure_verified=False,failed_asset_id=None,reporting_coverage_verified=False)


def nie_start(value,collected):
    """NIE gives day/month but no year; keep inferred year and ambiguous DST explicit."""
    try:
        raw=datetime.strptime(value,'%I:%M %p, %d %b').replace(year=collected.year)
        if raw.date()>collected.date():raw=raw.replace(year=collected.year-1)
        t=pd.Timestamp(raw).tz_localize('Europe/London',ambiguous='NaT',nonexistent='NaT')
        return t.tz_convert('UTC').isoformat() if pd.notna(t) else None,raw.isoformat(' ')
    except (ValueError,TypeError):return None,None


def nie_rows():
    converter=Transformer.from_crs(29903,4326,always_xy=True)
    for path in sorted((BASE/'raw/outages/nie_powercheck').glob('*.json')):
        collected=datetime.strptime(path.stem,'%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        for r in json.loads(path.read_text()).get('outageMessage',[]):
            if r.get('outageType')!='Fault':continue
            try:
                e,n=map(float,r['point']['coordinates'].split(','));lon,lat=converter.transform(e,n)
            except (ValueError,KeyError,TypeError):lon=lat=None
            start,local=nie_start(r.get('startTime'),collected)
            yield dict(event_key=f'NIE:POWERCHECK:{r["outageId"]}:{(local or str(collected.year))[:4]}',
                       source_event_key=r['outageId'],source_record_id=r['outageId'],operator='NIE',
                       reported_at_utc=start,reported_at_local=local,timezone_status='Europe_London_year_inferred_from_snapshot_DST_ambiguity_preserved',
                       latitude=lat,longitude=lon,place=r.get('postCode'),cause=r.get('causeMessage'),
                       customers_affected=float(r['numCustAffected']) if str(r.get('numCustAffected','')).isdigit() else None,
                       postcodes_json=json.dumps(postcodes(r.get('fullPostCodes'))),
                       postcode_sectors_as_published=r.get('fullPostCodes'),report_class='unverified_incident',
                       planned_status='published_fault_outageType',location_asset_id=None,
                       location_basis='NIE_published_Irish_grid_point_EPSG29903_to_WGS84',location_precision='published_outage_point_not_verified_asset',
                       restored_at_utc=None,estimated_restoration_at_utc=None,
                       source_kind='NIE_public_map_snapshot',source_resource=str(path.relative_to(ROOT)),
                       source_properties_json=json.dumps(r),archive_start_basis='published_time_year_inferred',archive_end_basis='ongoing_snapshot',
                       first_seen_utc=collected.isoformat(),last_seen_utc=collected.isoformat(),
                       evidence_flags=json.dumps(['year_inferred','restoration_unknown','collection_may_be_delayed']),
                       provenance_status='official_public_customer_map',calibration_role='regional_positive_observation_or_validation_only',
                       training_eligible=False,asset_failure_verified=False,failed_asset_id=None,reporting_coverage_verified=False)


def same_episode(record,candidate):
    """Require same normalised operator/ID and overlapping observations, not proximity."""
    if canonical_operator(record['operator'])!=canonical_operator(candidate['operator']):return False
    if record['source_event_key'].strip().upper()!=candidate['source_event_key'].strip().upper():return False
    point=utc(candidate.get('reported_at_utc'))
    start=utc(record.get('reported_start_as_supplied_utc') or record.get('reported_at_utc'))
    end=utc(record.get('last_seen_utc'))
    if not point or not start:return False
    t=pd.Timestamp(point);s=pd.Timestamp(start)
    if record.get('archive_start_basis')=='reported':return abs(t-s)<=pd.Timedelta(hours=1)
    return s<=t<=(pd.Timestamp(end) if end else s)


def assign_country(records):
    boundary=json.loads((BASE/'raw/uk_land_boundary.geojson').read_text())
    x=np.array([r.get('longitude') if r.get('longitude') is not None else np.nan for r in records]);y=np.array([r.get('latitude') if r.get('latitude') is not None else np.nan for r in records])
    names=np.full(len(records),'Unknown',dtype=object)
    for f in boundary['features']:
        polygon=shape(f['geometry']);shapely.prepare(polygon)
        names[shapely.intersects_xy(polygon,x,y)]=f['properties']['CTRY24NM']
    for r,country in zip(records,names):
        r['country']=country
        r['geographic_region']='Northern Ireland' if country=='Northern Ireland' else 'Southern Scotland' if country=='Scotland' and r['operator']=='SPEN' else 'Northern England' if country=='England' and r['operator'] in ('SPEN','NPG','ENWL') else country


def import_records(db,records):
    db.row_factory=sqlite3.Row
    old=[dict(r) for r in db.execute('SELECT event_key,source_event_key,operator,reported_at_utc,source_kind FROM outages')]
    existing={r['event_key'] for r in old};by_id=defaultdict(list)
    for r in old:
        if r['source_kind'] in ('live_snapshot','NIE_public_map_snapshot'):
            by_id[(canonical_operator(r['operator']),r['source_event_key'].strip().upper())].append(r)
    columns={r[1] for r in db.execute('PRAGMA table_info(outages)')}
    booleans={'reporting_coverage_verified'};numeric={'archive_duration_minutes'}
    for key in set().union(*(r.keys() for r in records))-columns:
        db.execute(f'ALTER TABLE outages ADD COLUMN {key} '+('INTEGER' if key in booleans else 'REAL' if key in numeric else 'TEXT'))
    db.execute('''CREATE TABLE IF NOT EXISTS outage_source_evidence(
        evidence_key TEXT PRIMARY KEY,event_key TEXT,source_record_id TEXT,source_kind TEXT,source_resource TEXT,
        archive_start_basis TEXT,archive_end_basis TEXT,properties_json TEXT)''')
    stats=Counter();evidence=[]
    for r in records:
        matches=by_id.get((r['operator'],r['source_event_key'].strip().upper()),[])
        matches=[m for m in matches if same_episode(r,m)]
        canonical=matches[0]['event_key'] if len(matches)==1 else r['event_key']
        if canonical!=r['event_key']:stats['matched_existing_episode']+=1
        elif canonical not in existing:
            keys=list(r);db.execute('INSERT INTO outages ('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')',[r[k] for k in keys])
            existing.add(canonical);stats['inserted']+=1
        else:stats['already_imported']+=1
        # Store source evidence even for merged reports, without overwriting stronger official attributes.
        db.execute('INSERT OR REPLACE INTO outage_source_evidence VALUES (?,?,?,?,?,?,?,?)',
                   (r['event_key'],canonical,r['source_record_id'],r['source_kind'],r['source_resource'],r['archive_start_basis'],r['archive_end_basis'],r['source_properties_json']))
        evidence.append(dict(r,canonical_event_key=canonical))
    db.execute('CREATE INDEX IF NOT EXISTS outage_evidence_event ON outage_source_evidence(event_key)')
    return dict(stats),pd.DataFrame(evidence)


def main():
    source=pd.read_csv(SOURCE,low_memory=False)
    if not source.record_id.is_unique:raise ValueError('Duplicate source record IDs require source review')
    print('Normalising supplied archive',len(source),'records',flush=True)
    archive=[normalise_archive(r) for r in source.to_dict('records')]
    current=list({r['event_key']:r for r in nie_rows()}.values());all_records=archive+current
    print('Applying UK land scope and region labels',flush=True);assign_country(all_records)
    mask=on_land(land_boundary(),[r['longitude'] for r in all_records],[r['latitude'] for r in all_records])
    accepted=[r for r,keep in zip(all_records,mask) if keep];excluded=[r for r,keep in zip(all_records,mask) if not keep]
    with sqlite3.connect(BASE/'uk_power_grid.sqlite') as db:
        print('Importing retained evidence',len(accepted),'records',flush=True)
        stats,evidence=import_records(db,accepted)
        db.commit()
        evidence.to_parquet(BASE/'normalized/supplemental_outage_evidence.parquet',index=False)
        pd.DataFrame(excluded).to_parquet(BASE/'normalized/excluded_supplemental_outages.parquet',index=False)
        pd.read_sql_query('SELECT * FROM outages',db).to_parquet(BASE/'normalized/all_outage_reports.parquet',index=False)
        active=dict(db.execute('SELECT operator,COUNT(*) FROM outages GROUP BY operator'))
    report=dict(generated_utc=now(),source_rows=len(source),official_NIE_snapshot_reports=len(current),
                retained_evidence_rows=len(accepted),excluded_missing_or_outside_UK_land=len(excluded),
                import_results=stats,active_reports_by_operator=active,
                imported_evidence_by_operator=dict(Counter(r['operator'] for r in accepted)),
                imported_evidence_by_target_region=dict(Counter(r['geographic_region'] for r in accepted)),
                start_basis_counts=dict(Counter(r['archive_start_basis'] for r in accepted)),
                restoration_basis_counts=dict(Counter(r['archive_end_basis'] for r in accepted)),
                strict_physical_failure_training_labels_added=0,
                user_preference='Use supplied events without reconstructing source collection periods',
                note='Source IDs and observed archive episodes retained; unknown periods do not create failure-free labels. Last sightings are not actual restoration.')
    write_json(BASE/'reports/supplemental_outages.json',report);print(json.dumps(report,indent=2))


if __name__=='__main__':main()
