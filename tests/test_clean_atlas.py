"""Electricity-only classification, complete point maps and honest outage evidence."""
import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest
from flask import Flask
from shapely.geometry import Point,LineString

from backend.asset_rules import classify_osm,display_name
from backend.grid import bp as grid_bp
from backend.catalog import bp as catalog_bp


@pytest.mark.parametrize('power',['manual','hydraulic','socket','outlet','EV Charging','gas_cabinet','yes','no','500 W','unknown'])
def test_non_grid_power_tags_are_not_electrical_components(power):
    assert classify_osm({'power':power})[0] is None


def test_shop_site_names_do_not_remove_real_substations():
    assert classify_osm({'power':'substation','name':'Tesco car park'})[0]=='substation'
    assert display_name('substation','Tesco car park')=='Substation · Tesco car park'
    assert classify_osm({'shop':'supermarket','name':'Power Shop'})[0] is None
    assert classify_osm({'power':'portal'})[0]=='gantry'
    assert classify_osm({'power':'generator','generator:source':'battery'})[0]=='storage'
    assert classify_osm({'power':'switch','switch':'circuit_breaker'})[0]=='breaker'
    assert classify_osm({'power':'plant','construction':'yes'})[0] is None


def test_heating_devices_are_excluded_but_combined_heat_and_power_is_retained():
    assert classify_osm({'power':'generator','generator:source':'solar','generator:method':'thermal'})[0] is None
    assert classify_osm({'power':'generator','generator:type':'heat_pump','generator:output:electricity':'no'})[0] is None
    assert classify_osm({'power':'plant','plant:output:electricity':'0 MW'})[0] is None
    assert classify_osm({'power':'plant','plant:output:hot_water':'6 MW'})[0] is None
    assert classify_osm({'power':'generator','generator:output:hot_water':'yes','generator:method':'photovoltaic'})[0]=='distributed_generator'
    assert classify_osm({'power':'generator','generator:method':'thermal','generator:output:electricity':'yes','generator:output:hot_water':'yes'})[0]=='distributed_generator'


@pytest.fixture()
def atlas(tmp_path):
    path=tmp_path/'catalog.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE nodes(node_id TEXT PRIMARY KEY,node_type TEXT,source TEXT,name TEXT,longitude REAL,latitude REAL,
            voltage_kv REAL,geometry_wgs84_wkb BLOB,properties_json TEXT,asset_category TEXT);
            CREATE VIRTUAL TABLE spatial_index USING rtree(id,min_lon,max_lon,min_lat,max_lat);
            CREATE VIRTUAL TABLE node_point_index USING rtree(id,min_lon,max_lon,min_lat,max_lat);
            CREATE TABLE metadata(key TEXT,value TEXT);''')
        for name in ['edges','relationships','candidate_edges']:
            db.execute(f'CREATE TABLE {name}(from_node TEXT,to_node TEXT,source TEXT,relation TEXT,evidence TEXT)')
        rows=[('station','substation','operator','Tesco site',0.,51.,11.,Point(0,51).wkb,'{}','substation'),
              ('line','power_line','OSM',None,1.5,51.,132.,LineString([(-1,51),(2,51)]).wkb,'{}','link'),
              ('battery','storage','operator','Battery',.2,51.2,33.,Point(.2,51.2).wkb,'{}','storage'),
              ('unlocated','transformer','CIM',None,None,None,33.,None,'{}','transformer')]
        db.executemany('INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)',rows)
        db.executemany('INSERT INTO node_point_index VALUES (?,?,?,?,?)',[(1,0,0,51,51),(2,1.5,1.5,51,51),(3,.2,.2,51.2,51.2)])
        db.executemany('INSERT INTO spatial_index VALUES (?,?,?,?,?)',[(1,0,0,51,51),(2,-1,2,51,51),(3,.2,.2,51.2,51.2)])
        db.execute('''CREATE TABLE node_cells AS SELECT CAST((longitude+180)*20 AS INTEGER) cell_x,CAST((latitude+90)*20 AS INTEGER) cell_y,
            asset_category,source,COUNT(*) count,SUM(longitude) longitude_sum,SUM(latitude) latitude_sum,MIN(node_id) sample_id
            FROM nodes WHERE longitude IS NOT NULL GROUP BY cell_x,cell_y,asset_category,source''')
        db.execute('INSERT INTO metadata VALUES (?,?)',('cleaning',json.dumps({'retained_nodes':4,'located_nodes':3})))
        base={'operator':'SEPD','place':'Primary','cause':'Wind','report_class':'unverified_incident','customers_affected':10,
              'damage':0,'planned_status':'unknown_not_published','training_eligible':False,'location_asset_id':'station',
              'location_basis':'candidate_feeder_representative_point','latitude':51.,'longitude':0.}
        outages=[{**base,'event_key':'e1','reported_at_local':'2020-01-01 12:00:00','reported_at_utc':'2020-01-01T12:00:00+00:00'},
                 {**base,'event_key':'e2','reported_at_local':'2021-01-01 12:00:00','reported_at_utc':None,'latitude':None,'longitude':None,'location_asset_id':None},
                 {**base,'event_key':'e3','reported_at_local':'2021-01-02 12:00:00','reported_at_utc':'2021-01-02T12:00:00+00:00','report_class':'possible_operational_or_planned'}]
        pd.DataFrame(outages).to_sql('outages',db,index=False)
        pd.DataFrame([{'node_id':'station','longitude':0.,'latitude':51.,'weather_key':'sample','distance_km':12.,'association_status':'coarse_regional_reanalysis_at_node'}]).to_sql('node_weather_links',db,index=False)
        db.executescript('''CREATE TABLE flood_outlines(outline_id TEXT,event_name TEXT,start_date TEXT,end_date TEXT,flood_source TEXT,source_quality TEXT,date_precision TEXT);
            CREATE TABLE node_flood_history(node_id TEXT,outline_id TEXT);
            INSERT INTO flood_outlines VALUES ('flood1','Recorded flood','2021-01-01','2021-01-02','river','as_published','source_date_range_precision_unverified');
            INSERT INTO node_flood_history VALUES ('station','flood1');''')
    weather=tmp_path/'weather';weather.mkdir()
    pd.DataFrame({'time':pd.to_datetime(['2021-01-01T12:00Z']), 'wind_speed_10m':[40.], 'wind_gusts_10m':[70.],
                  'wind_direction_10m':[180], 'temperature_2m':[10.], 'precipitation':[2.]}).to_parquet(weather/'sample.parquet',index=False)
    app=Flask(__name__);app.config.update(TESTING=True,GRID_DATABASE=path,WEATHER_DIRECTORY=weather)
    app.register_blueprint(grid_bp);app.register_blueprint(catalog_bp)
    return app.test_client(),path


def test_complete_country_overview_and_source_filter(atlas):
    client,_=atlas
    d=client.get('/api/grid/overview?bbox=-2,50,3,52&zoom=6').get_json()
    assert d['represented_count']==3 and d['truncated'] is False
    assert sum(f['properties']['count'] for f in d['features'])==3
    assert client.get('/api/grid/overview?bbox=-2,50,3,52&zoom=5.091').status_code==200
    filtered=client.get('/api/grid/overview?bbox=-2,50,3,52&zoom=6&categories=storage').get_json()
    assert filtered['represented_count']==1
    assert filtered['features'][0]['properties']['node_id']=='battery'


def test_point_weather_map_does_not_use_crossing_line_geometry(atlas):
    client,_=atlas
    for zoom in [6,14]:
        d=client.get(f'/api/grid/overview?bbox=-.1,50.9,.3,51.3&zoom={zoom}').get_json()
        assert d['represented_count']==2
    assert client.get('/api/grid/node-list?bbox=-.1,50.9,.3,51.3').get_json()['items'][0]['node_id']=='battery'


def test_dense_locations_are_aggregated_without_losing_nodes(atlas):
    client,path=atlas
    with sqlite3.connect(path) as db:
        db.executemany("INSERT INTO nodes(node_id,node_type,source,longitude,latitude,asset_category) VALUES (?,'substation','A',.5,50.5,'substation')",[(f'dense:{i}',) for i in range(3500)])
        db.execute("INSERT INTO node_point_index SELECT rowid,longitude,longitude,latitude,latitude FROM nodes WHERE node_id LIKE 'dense:%'")
    d=client.get('/api/grid/overview?bbox=.4,50.4,.6,50.6&zoom=18').get_json()
    assert d['represented_count']==3500 and d['truncated'] is False
    assert len(d['features'])<3000


def test_all_history_includes_unmapped_and_possible_operational_reports(atlas):
    client,_=atlas
    d=client.get('/api/grid/outages/summary').get_json()
    assert d['total']==3 and d['mapped']==2 and d['unresolved_utc']==1
    assert sum(m['count'] for m in d['months'])==3
    assert d['training_eligible']==0
    assert client.get('/api/grid/outages/summary?classification=possible_operational_or_planned').get_json()['total']==1
    assert client.get('/api/grid/outages?location=unmapped').get_json()['items'][0]['event_key']=='e2'
    assert client.get('/api/grid/outages/overview?bbox=-2,50,3,52&zoom=6').get_json()['represented_count']==2


def test_local_report_date_filters_are_inclusive_and_validate_input(atlas):
    client,_=atlas
    assert client.get('/api/grid/outages/summary?start=2021-01-01&end=2021-01-01').get_json()['total']==1
    assert client.get('/api/grid/outages/summary?start=2022-01-01&end=2021-01-01').status_code==400
    assert client.get('/api/grid/outages?limit=201').status_code==400
    assert client.get('/api/grid/outages/summary?location=wrong').status_code==400


def test_weather_and_dated_flood_evidence_preserve_unknown_depth(atlas):
    client,_=atlas
    d=client.get('/api/grid/weather?id=station&at=2021-01-01T12:24:00Z').get_json()
    assert d['weather']['wind_gust_kmh']==70
    assert d['weather']['precipitation_preceding_hour_mm']==2
    assert d['weather']['valid_time_utc'].startswith('2021-01-01T12:00')
    assert d['flood_depth_m'] is None and len(d['flood_evidence']['events'])==1
    later=client.get('/api/grid/weather?id=station&at=2021-01-03T12:00:00Z').get_json()
    assert later['weather'] is None and later['flood_evidence']['events']==[]
    assert later['flood_evidence']['absence_means']=='unknown'
    assert client.get('/api/grid/weather?id=station&at=2021-01-01T12:00').status_code==400


def test_search_is_component_specific_and_cannot_expand_wildcards(atlas):
    client,_=atlas
    d=client.get('/api/grid/find?q=Tesco').get_json()
    assert d['items'][0]['display_name']=='Substation · Tesco site'
    assert client.get('/api/grid/find?q=%25').get_json()['items']==[]
    assert client.get('/api/grid/overview?bbox=nan,0,1,2').status_code==400
    assert client.get('/api/grid/overview?zoom=21').status_code==400
    assert client.get('/api/grid/unlocated').get_json()['items'][0]['node_id']=='unlocated'


def test_cleaned_database_removes_junk_and_keeps_source_assets(tmp_path,monkeypatch):
    import scripts.clean_power_grid as cleaner
    monkeypatch.setattr(cleaner,'BASE',tmp_path)
    (tmp_path/'normalized').mkdir();(tmp_path/'reports').mkdir()
    source=tmp_path/'source.sqlite';target=tmp_path/'clean.sqlite'
    with sqlite3.connect(source) as db:
        db.executescript('''CREATE TABLE nodes(node_id TEXT PRIMARY KEY,node_type TEXT,source TEXT,name TEXT,longitude REAL,latitude REAL,properties_json TEXT);
            CREATE VIRTUAL TABLE spatial_index USING rtree(id,min_lon,max_lon,min_lat,max_lat);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);''')
        for name in ['edges','relationships','candidate_edges']:db.execute(f'CREATE TABLE {name}(from_node TEXT,to_node TEXT,source TEXT,relation TEXT,evidence TEXT)')
        db.executemany('INSERT INTO nodes VALUES (?,?,?,?,?,?,?)',[
            ('station','substation','NPG','Tesco site',0.,51.,'{}'),('battery','generation','OSM','Battery',.1,51.,'{"power":"plant","plant:source":"battery"}'),
            ('gate','other_power_equipment','OSM','Manual lock',.2,51.,'{"power":"manual"}'),('u','transformer','CIM',None,None,None,'{}')])
        db.executemany('INSERT INTO spatial_index VALUES (?,?,?,?,?)',[(1,0,0,51,51),(2,.1,.1,51,51),(3,.2,.2,51,51)])
        db.execute("INSERT INTO relationships VALUES ('station','gate','OSM','within_site','source')")
    report=cleaner.clean_database(source,target)
    assert report['retained_nodes']==3 and report['removed_nodes']==1
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT node_type FROM nodes WHERE node_id='battery'").fetchone()[0]=='storage'
        assert db.execute("SELECT name FROM nodes WHERE node_id='station'").fetchone()[0]=='Tesco site'
        assert db.execute('SELECT COUNT(*) FROM relationships').fetchone()[0]==0
        assert db.execute('SELECT SUM(count) FROM node_cells').fetchone()[0]==2
    with sqlite3.connect(source) as db:assert db.execute('SELECT count(*) FROM nodes').fetchone()[0]==4
