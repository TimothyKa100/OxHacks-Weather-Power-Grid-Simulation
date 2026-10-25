import json
import sqlite3

import pandas as pd
import pytest
from shapely.geometry import Polygon

from backend.asset_rules import minimum_zoom, tower_height
from scripts.refine_uk_grid import on_land
from scripts.build_uk_outages import locate, postcodes, record, timestamp


def test_hierarchy_retains_major_assets_and_delays_frequent_equipment():
    assert minimum_zoom('generation') == 4
    assert minimum_zoom('substation',400) == 4
    assert minimum_zoom('substation',11) > minimum_zoom('substation',132)
    assert minimum_zoom('tower') == 13
    assert minimum_zoom('pole') == minimum_zoom('junction') == 15
    assert minimum_zoom('distributed_generator',capacity=100) < minimum_zoom('distributed_generator',capacity=.004)


def test_height_classes_and_unknowns_do_not_invent_measurements():
    assert tower_height({},132) == (26.,26.,26.,'assumed_132kV_L4_26m')
    assert tower_height({},400)[:3] == (46.,42.,50.)
    assert tower_height({'design':'T-pylon'},400)[:3] == (35.,35.,35.)
    assert tower_height({'design:ref':'L4'},None)[0] == 26
    assert tower_height({'height':'48 m'},400)[0] == 48
    assert tower_height({},None)[0] is None


def test_scope_uses_land_geometry_and_rejects_null_nan_and_ocean():
    land=Polygon([(0,0),(2,0),(2,2),(0,2)])
    assert on_land(land,[1,3,None,float('nan'),0],[1,1,1,1,1]).tolist()==[True,False,False,False,True]


def test_postcode_geocoding_remains_area_evidence_and_missing_is_excluded():
    land=Polygon([(0,0),(2,0),(2,2),(0,2)])
    r=record('UKPN','test','2026-10-24T12:00:00',{},longitude=None,latitude=None,
             postcodes_json=json.dumps(['SW1A1AA']),published_planned=False)
    accepted,excluded=locate([r],land,{'SW1A1AA':{'longitude':1,'latitude':1}})
    assert len(accepted)==1 and not excluded
    assert accepted[0]['location_basis']=='affected_postcode_centroid_or_representative'
    assert accepted[0]['failed_asset_id'] is None and not accepted[0]['training_eligible']
    assert accepted[0]['planned_status']=='published_unplanned'
    missing=record('NPG','test','2026-10-24T12:00:00Z',{},longitude=None,latitude=None,
                   postcodes_json='[]',published_planned=None)
    assert len(locate([missing],land,{})[1])==1


def test_local_time_and_restoration_are_not_silently_invented():
    assert timestamp('2026-07-01T12:00:00')[1].startswith('2026-07-01T11:00')
    assert timestamp('2026-10-25T01:30:00')[1] is None  # repeated clock hour
    assert timestamp('2026-10-25T01:30:00Z')[1] is not None
    assert postcodes('SW1A 1AA;SW1A1AA;not a postcode')==['SW1A1AA']


def test_network_routes_and_schematic_connections_are_distinct(tmp_path):
    from flask import Flask
    from shapely.geometry import LineString
    from backend.catalog import bp
    path=tmp_path/'grid.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE nodes(node_id TEXT PRIMARY KEY,node_type TEXT,source TEXT,name TEXT,
            longitude REAL,latitude REAL,voltage_kv REAL,geometry_wgs84_wkb BLOB,min_zoom INTEGER);
            CREATE VIRTUAL TABLE spatial_index USING rtree(id,min_lon,max_lon,min_lat,max_lat);
            CREATE VIRTUAL TABLE node_point_index USING rtree(id,min_lon,max_lon,min_lat,max_lat);
            CREATE TABLE edges(from_node TEXT,to_node TEXT,source TEXT,relation TEXT,evidence TEXT);''')
        db.executemany('INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?)',[
            ('line','power_line','A',None,0.,51.,400.,LineString([(0,51),(.1,51.1)]).wkb,4),
            ('end','junction','A',None,.1,51.1,400.,None,15)])
        db.execute('INSERT INTO spatial_index VALUES (1,0,.1,51,51.1)')
        db.executemany('INSERT INTO node_point_index VALUES (?,?,?,?,?)',[(1,0,0,51,51),(2,.1,.1,51.1,51.1)])
        db.execute("INSERT INTO edges VALUES ('line','end','A','line_terminal','source')")
    app=Flask(__name__);app.config['GRID_DATABASE']=path;app.register_blueprint(bp);client=app.test_client()
    route=client.get('/api/grid/network?kind=routes&zoom=6&hierarchical=1').get_json()
    assert route['features'][0]['properties']['representation']=='mapped_or_published_route'
    assert client.get('/api/grid/network?kind=edges&zoom=6').get_json()['features']==[]
    links=client.get('/api/grid/network?kind=edges&zoom=15&source=A').get_json()
    assert links['features'][0]['properties']['representation']=='topology_schematic_not_surveyed_route'
    assert client.get('/api/grid/network?kind=edges&zoom=15&source=B').get_json()['features']==[]
    assert client.get('/api/grid/network?kind=edges&after=-1').status_code==400
