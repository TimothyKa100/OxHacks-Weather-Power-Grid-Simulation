"""Complete point overviews, full recorded outage history and node-location conditions."""
from contextlib import closing
from datetime import datetime, timedelta
from functools import lru_cache
import json
import math
from pathlib import Path
import sqlite3

from flask import Blueprint, current_app, jsonify, request, Response, stream_with_context

from .grid import connect, ROOT, feature
from .asset_rules import CATEGORIES, TYPE_CATEGORY, display_name

bp = Blueprint('catalog', __name__, url_prefix='/api/grid')


@bp.errorhandler(sqlite3.OperationalError)
def unavailable(error):
    current_app.logger.warning('Prepared grid catalog unavailable: %s',error)
    return jsonify(error='Prepared grid catalog unavailable. Run scripts/clean_power_grid.py and scripts/prepare_grid_conditions.py.'),503


def bbox():
    try:
        values = [float(x) for x in request.args.get('bbox','-9,49,3,61.5').split(',')]
        if len(values)!=4 or not all(math.isfinite(x) for x in values): raise ValueError()
        w,s,e,n=values
        if not(-180<=w<e<=180 and -90<=s<n<=90): raise ValueError()
        return values
    except ValueError:
        raise ValueError('bbox needs increasing finite west,south,east,north coordinates') from None


def zoom():
    try:
        z=float(request.args.get('zoom',6))
        if not math.isfinite(z) or not 3<=z<=20: raise ValueError()
        return math.floor(z)
    except ValueError: raise ValueError('zoom must be finite and from 3 to 20') from None


def node_filters(alias='n'):
    groups=request.args.get('categories',','.join(CATEGORIES)).split(',')
    if not groups or any(x not in CATEGORIES for x in groups): raise ValueError('Unknown asset category')
    kinds=[t for g in groups for t in CATEGORIES[g]['types']]
    clauses=[alias+'.node_type IN ('+','.join('?' for _ in kinds)+')']
    values=list(kinds)
    source=request.args.get('source')
    if source and source!='all': clauses.append(alias+'.source=?');values.append(source)
    if request.args.get('hierarchical') == '1':
        clauses.append(alias+'.min_zoom<=?');values.append(zoom())
    return clauses,values,groups


def points_response(rows, step, kind, covered_bbox=None):
    features=[]
    for row in rows:
        r=dict(row)
        count=int(r['count'])
        properties={'count':count,'kind':kind if count==1 else 'cluster'}
        if kind=='node':
            properties.update(node_id=r.get('sample_id') if count==1 else None,
                              category=r.get('category'),categories=json.loads(r.get('category_counts') or '{}'))
        else:
            properties.update(event_key=r.get('sample_id') if count==1 else None)
        properties['bounds']=[r['bx']*step-180,r['by']*step-90,(r['bx']+1)*step-180,(r['by']+1)*step-90]
        features.append({'type':'Feature','geometry':{'type':'Point','coordinates':[r['longitude'],r['latitude']]},'properties':properties})
    return {'type':'FeatureCollection','features':features,'represented_count':sum(int(r['count']) for r in rows),
            'aggregation':'node_location_points','cell_degrees':step,'covered_bbox':covered_bbox,'truncated':False}


@bp.get('/overview')
def overview():
    try:
        w,s,e,n=bbox();z=zoom();clauses,values,groups=node_filters()
    except ValueError as error: return jsonify(error=str(error)),400
    with closing(connect()) as db:
        if z<=10:
            stride=max(1,round((.25*2**(6-z))/.05))
            filters=['cell_x BETWEEN ? AND ?','cell_y BETWEEN ? AND ?', 'asset_category IN ('+','.join('?' for _ in groups)+')']
            params=[math.floor((w+180)*20),math.floor((e+180)*20),math.floor((s+90)*20),math.floor((n+90)*20),*groups]
            source=request.args.get('source')
            if source and source!='all':filters.append('source=?');params.append(source)
            cell_table='node_cells'
            if request.args.get('hierarchical')=='1':
                cell_table='node_zoom_cells';filters.append('min_zoom<=?');params.append(z)
            while True:
                sql='''WITH buckets AS (SELECT CAST(cell_x/? AS INTEGER) bx,CAST(cell_y/? AS INTEGER) by,
                    asset_category,SUM(count) count,SUM(longitude_sum) longitude_sum,SUM(latitude_sum) latitude_sum,MIN(sample_id) sample_id
                    FROM '''+cell_table+''' WHERE '''+' AND '.join(filters)+''' GROUP BY bx,by,asset_category)
                    SELECT bx,by,SUM(count) count,SUM(longitude_sum)/SUM(count) longitude,SUM(latitude_sum)/SUM(count) latitude,
                    MIN(sample_id) sample_id,json_group_object(asset_category,count) category_counts FROM buckets GROUP BY bx,by LIMIT 3001'''
                rows=db.execute(sql,[stride,stride,*params]).fetchall()
                if len(rows)<=3000:break
                stride*=2
            return jsonify(points_response(rows,stride*.05,'node',[w,s,e,n]))
        step=max(.000002,.05*2**(10-z))
        spatial=['p.min_lon<=?','p.max_lon>=?','p.min_lat<=?','p.max_lat>=?', 'n.longitude BETWEEN ? AND ?','n.latitude BETWEEN ? AND ?']
        params=[e,w,n,s,w,e,s,n,*values]
        while True:
            sql='''WITH buckets AS (SELECT CAST((n.longitude+180)/? AS INTEGER) bx,CAST((n.latitude+90)/? AS INTEGER) by,
                n.asset_category,COUNT(*) count,SUM(n.longitude) longitude_sum,SUM(n.latitude) latitude_sum,MIN(n.node_id) sample_id
                FROM node_point_index p CROSS JOIN nodes n ON n.rowid=p.id WHERE '''+' AND '.join(spatial+clauses)+''' GROUP BY bx,by,n.asset_category)
                SELECT bx,by,SUM(count) count,SUM(longitude_sum)/SUM(count) longitude,SUM(latitude_sum)/SUM(count) latitude,
                MIN(sample_id) sample_id,json_group_object(asset_category,count) category_counts FROM buckets GROUP BY bx,by LIMIT 3001'''
            rows=db.execute(sql,[step,step,*params]).fetchall()
            if len(rows)<=3000:break
            step*=2
        return jsonify(points_response(rows,step,'node',[w,s,e,n]))


def outage_filters(alias='o'):
    clauses,values=[],[]
    for key,operation in [('start','>='),('end','<')]:
        raw=request.args.get(key)
        if raw:
            try: dt=datetime.strptime(raw,'%Y-%m-%d')
            except ValueError: raise ValueError(key+' must be YYYY-MM-DD') from None
            if key=='end':dt+=timedelta(days=1)
            clauses.append(f'{alias}.reported_at_local{operation}?');values.append(dt.isoformat(' '))
    if request.args.get('start') and request.args.get('end') and request.args['start']>request.args['end']:
        raise ValueError('start must not follow end')
    for key,column in [('operator','operator'),('cause','cause'),('classification','report_class'),('region','geographic_region')]:
        value=request.args.get(key)
        if value and value!='all': clauses.append(f'{alias}.{column}=?');values.append(value)
    location=request.args.get('location','all')
    if location not in ('all','mapped','unmapped'):raise ValueError('location must be all, mapped or unmapped')
    if location!='all':clauses.append(alias+'.latitude IS '+('NOT NULL' if location=='mapped' else 'NULL'))
    asset=request.args.get('asset_id')
    if asset:clauses.append(alias+'.location_asset_id=?');values.append(asset)
    return (' AND '.join(clauses) if clauses else '1=1'),values


@bp.get('/outages/summary')
def outage_summary():
    try:where,values=outage_filters()
    except ValueError as error:return jsonify(error=str(error)),400
    with closing(connect()) as db:
        totals=dict(db.execute('''SELECT COUNT(*) total,SUM(latitude IS NOT NULL) mapped,
            MIN(reported_at_local) earliest,MAX(reported_at_local) latest,SUM(training_eligible) training_eligible,
            SUM(reported_at_utc IS NULL) unresolved_utc FROM outages o WHERE '''+where,values).fetchone())
        months=[dict(r) for r in db.execute("SELECT substr(reported_at_local,1,7) month,COUNT(*) count FROM outages o WHERE "+where+' GROUP BY month ORDER BY month',values)]
        causes=[dict(r) for r in db.execute('SELECT cause,COUNT(*) count FROM outages o WHERE '+where+' GROUP BY cause ORDER BY count DESC',values)]
        operators=[dict(r) for r in db.execute('SELECT operator,COUNT(*) count FROM outages o WHERE '+where+' GROUP BY operator',values)]
    return jsonify(**totals,months=months,causes=causes,operators=operators,
                   scope='Downloaded historical incident reports and archived operator live snapshots; coverage varies by source',
                   location_note='Feeder/circuit proxies or affected-area/postcode locations; never verified failed components')


@bp.get('/outages/overview')
def outage_overview():
    try:w,s,e,n=bbox();z=zoom();where,values=outage_filters()
    except ValueError as error:return jsonify(error=str(error)),400
    step=max(.00001,.25*2**(6-z))
    with closing(connect()) as db:
        while True:
            rows=db.execute('''SELECT CAST((longitude+180)/? AS INTEGER) bx,CAST((latitude+90)/? AS INTEGER) by,
                COUNT(*) count,AVG(longitude) longitude,AVG(latitude) latitude,MIN(event_key) sample_id
                FROM outages o WHERE longitude BETWEEN ? AND ? AND latitude BETWEEN ? AND ? AND '''+where+''' GROUP BY bx,by LIMIT 3001''',
                [step,step,w,e,s,n,*values]).fetchall()
            if len(rows)<=3000:break
            step*=2
    return jsonify(points_response(rows,step,'outage',[w,s,e,n]))


@bp.get('/outages')
def outages():
    try:
        where,values=outage_filters();limit=int(request.args.get('limit',50));offset=int(request.args.get('offset',0))
        if not 1<=limit<=200 or offset<0:raise ValueError('limit must be 1–200 and offset nonnegative')
        if request.args.get('bbox'):
            w,s,e,n=bbox();where+=' AND longitude BETWEEN ? AND ? AND latitude BETWEEN ? AND ?';values += [w,e,s,n]
    except ValueError as error:return jsonify(error=str(error)),400
    with closing(connect()) as db:
        rows=db.execute('''SELECT event_key,operator,reported_at_local,reported_at_utc,place,cause,report_class,
            latitude,longitude,location_asset_id,location_basis,customers_affected,damage,planned_status
            FROM outages o WHERE '''+where+' ORDER BY reported_at_local DESC,event_key LIMIT ? OFFSET ?',[*values,limit+1,offset]).fetchall()
    return jsonify(items=[dict(r) for r in rows[:limit]],next_offset=offset+limit if len(rows)>limit else None)


@bp.get('/outage')
def outage():
    with closing(connect()) as db:
        row=db.execute('SELECT * FROM outages WHERE event_key=?',(request.args.get('id',''),)).fetchone()
        candidates=[]
        evidence=[]
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='outage_circuit_candidates'").fetchone():
            candidates=[dict(r) for r in db.execute('SELECT * FROM outage_circuit_candidates WHERE event_key=? ORDER BY rank,distance_m LIMIT 100',
                                                   (request.args.get('id',''),))]
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='outage_source_evidence'").fetchone():
            evidence=[dict(r) for r in db.execute('''SELECT evidence_key,source_record_id,source_kind,source_resource,
                archive_start_basis,archive_end_basis FROM outage_source_evidence WHERE event_key=?''',(request.args.get('id',''),))]
    if row is None:return jsonify(error='Outage report not found'),404
    return jsonify(dict(row)|{'circuit_candidates':candidates,'source_evidence':evidence})


@lru_cache(maxsize=1024)
def weather_sample(directory,key,hour):
    import pandas as pd
    path=Path(directory)/f'{key}.parquet'
    if not path.exists():return None
    t=pd.Timestamp(hour)
    d=pd.read_parquet(path,columns=['time','wind_speed_10m','wind_gusts_10m','wind_direction_10m','temperature_2m','precipitation'],filters=[('time','==',t)])
    if d.empty:return None
    r=d.iloc[0]
    return {'valid_time_utc':t.isoformat(),'wind_speed_kmh':float(r.wind_speed_10m),'wind_gust_kmh':float(r.wind_gusts_10m),
            'wind_direction_degrees':int(r.wind_direction_10m),'temperature_c':float(r.temperature_2m),
            'precipitation_preceding_hour_mm':float(r.precipitation)}


@bp.get('/weather')
def weather():
    try:
        raw=request.args.get('at','2021-11-26T12:00:00Z')
        t=datetime.fromisoformat(raw.replace('Z','+00:00'))
        if t.utcoffset() is None:raise ValueError('at needs an explicit timezone')
        from datetime import timezone
        t=t.astimezone(timezone.utc).replace(minute=0,second=0,microsecond=0)
    except ValueError as error:return jsonify(error=str(error)),400
    with closing(connect()) as db:
        row=db.execute('SELECT * FROM node_weather_links WHERE node_id=?',(request.args.get('id',''),)).fetchone()
        has_floods=db.execute("SELECT 1 FROM sqlite_master WHERE name='node_flood_history'").fetchone() is not None
        flood_events=[]
        flood_history=[]
        flood_total=0
        if has_floods:
            flood_events=[dict(r) for r in db.execute('''SELECT f.outline_id,f.event_name,f.start_date,f.end_date,f.flood_source,f.source_quality,f.date_precision
                FROM node_flood_history h JOIN flood_outlines f ON f.outline_id=h.outline_id WHERE h.node_id=?
                AND f.start_date<=? AND COALESCE(f.end_date,f.start_date)>=? ORDER BY f.start_date LIMIT 50''',
                (request.args.get('id',''),t.date().isoformat(),t.date().isoformat()))]
            flood_total=db.execute('SELECT COUNT(*) FROM node_flood_history WHERE node_id=?',(request.args.get('id',''),)).fetchone()[0]
            flood_history=[dict(r) for r in db.execute('''SELECT f.outline_id,f.event_name,f.start_date,f.end_date,f.flood_source,f.date_precision
                FROM node_flood_history h JOIN flood_outlines f ON f.outline_id=h.outline_id WHERE h.node_id=?
                ORDER BY f.start_date DESC LIMIT 5''',(request.args.get('id',''),))]
    if row is None:return jsonify(error='No retained node with that ID'),404
    link=dict(row)
    sample=weather_sample(str(current_app.config.get('WEATHER_DIRECTORY',ROOT/'data/processed/weather')),link['weather_key'],t.isoformat()) if link['weather_key'] else None
    return jsonify(node_id=link['node_id'],point={'longitude':link['longitude'],'latitude':link['latitude']},
                   association=link,weather=sample,source='ERA5 historical reanalysis via Open-Meteo',
                   spatial_resolution_degrees=.25,mode='historical_node_point_estimate',
                   flood_depth_m=None,flood_status='No verified historical inundation-depth observations',
                   flood_evidence={'events':flood_events,'history':flood_history,'all_time_outline_count':flood_total,'coverage':'England recorded flood footprints; incomplete',
                                   'timing':'Source date ranges; exact timing/precision unverified','absence_means':'unknown'} if has_floods else None,
                   note='A nearby weather-grid estimate at the node location; not a measurement at the asset. Precipitation is not flood depth.')


@bp.get('/find')
def find():
    text=request.args.get('q','').strip()
    if not text or len(text)>200:return jsonify(error='Enter an asset ID or name, up to 200 characters'),400
    with closing(connect()) as db:
        rows=db.execute("SELECT node_id,node_type,source,name,longitude,latitude FROM nodes WHERE node_id=? OR name LIKE ? ESCAPE '!' LIMIT 30",
                        (text,text.replace('!','!!').replace('%','!%').replace('_','!_')+'%')).fetchall()
    return jsonify(items=[{**dict(r),'display_name':display_name(r['node_type'],r['name'])} for r in rows if r['node_type'] in TYPE_CATEGORY])


@bp.get('/unlocated')
def unlocated():
    try:
        offset=int(request.args.get('offset',0))
        if offset<0:raise ValueError()
    except ValueError:return jsonify(error='Invalid offset'),400
    with closing(connect()) as db:
        rows=db.execute('SELECT node_id,node_type,name,source FROM nodes WHERE latitude IS NULL OR longitude IS NULL ORDER BY node_id LIMIT 51 OFFSET ?',(offset,)).fetchall()
    return jsonify(items=[{**dict(r),'display_name':display_name(r['node_type'],r['name'])} for r in rows[:50]],next_offset=offset+50 if len(rows)>50 else None)


@bp.get('/node-list')
def node_list():
    try:
        w,s,e,n=bbox();clauses,values,_=node_filters();offset=int(request.args.get('offset',0))
        if offset<0:raise ValueError('Invalid offset')
    except ValueError as error:return jsonify(error=str(error)),400
    with closing(connect()) as db:
        rows=db.execute('''SELECT n.node_id,n.node_type,n.source,n.name,n.longitude,n.latitude
            FROM node_point_index p CROSS JOIN nodes n ON n.rowid=p.id
            WHERE p.min_lon<=? AND p.max_lon>=? AND p.min_lat<=? AND p.max_lat>=?
            AND n.longitude BETWEEN ? AND ? AND n.latitude BETWEEN ? AND ? AND '''+' AND '.join(clauses)+
            ' ORDER BY n.node_id LIMIT 51 OFFSET ?',[e,w,n,s,w,e,s,n,*values,offset]).fetchall()
    return jsonify(items=[{**dict(r),'display_name':display_name(r['node_type'],r['name'])} for r in rows[:50]],next_offset=offset+50 if len(rows)>50 else None)


@bp.get('/network')
def network():
    """Paged physical routes and explicitly schematic source topology.

    No nearest-neighbour lines or cross-source electrical identity are invented.
    Mechanical support relationships remain separate from conducting edges.
    """
    try:
        w,s,e,n=bbox();z=zoom();clauses,values,groups=node_filters()
        after=int(request.args.get('after',0));limit=int(request.args.get('limit',2500))
        kind=request.args.get('kind','routes')
        if after<0 or not 1<=limit<=3000 or kind not in ('routes','edges'):raise ValueError('Invalid network pagination or kind')
    except ValueError as error:return jsonify(error=str(error)),400
    features=[]
    with closing(connect()) as db:
        if kind=='routes':
            rows=db.execute('''SELECT n.rowid,n.node_id,n.node_type,n.source,n.name,n.longitude,n.latitude,
                n.voltage_kv,n.geometry_wgs84_wkb FROM spatial_index p CROSS JOIN nodes n ON n.rowid=p.id
                WHERE p.min_lon<=? AND p.max_lon>=? AND p.min_lat<=? AND p.max_lat>=?
                AND n.rowid>? AND n.node_type='power_line' AND n.geometry_wgs84_wkb IS NOT NULL AND '''+
                ' AND '.join(clauses)+' ORDER BY n.rowid LIMIT ?', [e,w,n,s,after,*values,limit+1]).fetchall()
            for r in rows[:limit]:
                f=feature(r);f['properties']['representation']='mapped_or_published_route';features.append(f)
        elif z<11:
            rows=[]
        else:
            source=request.args.get('source');where='';extra=[]
            if source and source!='all':where=' AND a.source=? AND b.source=?';extra=[source,source]
            types=[t for g in groups for t in CATEGORIES[g]['types']]
            placeholders=','.join('?' for _ in types)
            # Two indexed traversals ensure both directions of a visible connection are found.
            rows=db.execute('''WITH visible AS (SELECT n.node_id FROM node_point_index p CROSS JOIN nodes n ON n.rowid=p.id
                WHERE p.min_lon<=? AND p.max_lon>=? AND p.min_lat<=? AND p.max_lat>=?
                AND n.longitude BETWEEN ? AND ? AND n.latitude BETWEEN ? AND ?), ids AS (
                SELECT e.rowid id FROM visible v JOIN edges e ON e.from_node=v.node_id WHERE e.rowid>?
                UNION SELECT e.rowid id FROM visible v JOIN edges e ON e.to_node=v.node_id WHERE e.rowid>?)
                SELECT e.rowid,e.*,a.longitude ax,a.latitude ay,b.longitude bx,b.latitude by
                FROM ids JOIN edges e ON e.rowid=ids.id JOIN nodes a ON a.node_id=e.from_node JOIN nodes b ON b.node_id=e.to_node
                WHERE a.longitude IS NOT NULL AND b.longitude IS NOT NULL
                AND a.node_type IN ('''+placeholders+') AND b.node_type IN ('+placeholders+')'+where+
                ' ORDER BY e.rowid LIMIT ?', [e,w,n,s,w,e,s,n,after,after,*types,*types,*extra,limit+1]).fetchall()
            for r in rows[:limit]:
                features.append({'type':'Feature','id':f'edge:{r["rowid"]}',
                                 'geometry':{'type':'LineString','coordinates':[[r['ax'],r['ay']],[r['bx'],r['by']]]},
                                 'properties':{k:r[k] for k in ['from_node','to_node','source','relation','evidence']}
                                     | {'representation':'topology_schematic_not_surveyed_route'}})
    return jsonify(type='FeatureCollection',features=features,kind=kind,
                   next_after=rows[limit-1]['rowid'] if len(rows)>limit else None,
                   truncated=len(rows)>limit)


@bp.get('/generation-outages')
def generation_outages():
    """Separate unit-unavailability evidence; never counted as customer power cuts."""
    try:
        offset=int(request.args.get('offset',0))
        if offset<0:raise ValueError('offset must be nonnegative')
    except ValueError as error:return jsonify(error=str(error)),400
    clauses=[];values=[]
    for key,column in [('unit_id','unit_id'),('asset_id','station_node_id')]:
        if request.args.get(key):clauses.append(column+'=?');values.append(request.args[key])
    where=' AND '.join(clauses) or '1=1'
    with closing(connect()) as db:
        rows=db.execute('SELECT * FROM generation_outages WHERE '+where+' ORDER BY start_utc DESC,mrid LIMIT 51 OFFSET ?',[*values,offset]).fetchall()
    return jsonify(items=[dict(r) for r in rows[:50]],next_offset=offset+50 if len(rows)>50 else None,
                   scope='Elexon unit unavailability; separate from customer interruptions',
                   identity_note='Published unit IDs; station links are Atlas crosswalk candidates')
