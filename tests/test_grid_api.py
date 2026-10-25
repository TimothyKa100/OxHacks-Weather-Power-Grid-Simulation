"""Atlas tests with a tiny spatial graph, independent of multi-GB data files."""
import json
import sqlite3
import pytest
from flask import Flask
from shapely.geometry import LineString, Point
from backend.grid import bp


@pytest.fixture()
def client(tmp_path):
    path = tmp_path / 'grid.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('''
        CREATE TABLE nodes(node_id TEXT PRIMARY KEY,node_type TEXT,source TEXT,name TEXT,
            longitude REAL,latitude REAL,voltage_kv REAL,geometry_wgs84_wkb BLOB,properties_json TEXT);
        CREATE VIRTUAL TABLE spatial_index USING rtree(id,min_lon,max_lon,min_lat,max_lat);
        CREATE TABLE metadata(key TEXT,value TEXT);
        ''')
        for table in ['edges', 'relationships', 'candidate_edges']:
            db.execute(f'CREATE TABLE {table}(from_node TEXT,to_node TEXT,source TEXT,relation TEXT,evidence TEXT)')
        rows = [
            ('station', 'substation', 'A', 'Site', 0, 51, 132, Point(0, 51).wkb, '{}'),
            ('line', 'power_line', 'A', 'Crossing line', 2, 51, None, LineString([(-1, 51), (2, 51)]).wkb, '{}'),
            ('unlocated', 'transformer', 'A', None, None, None, None, None, '{"rating": "unknown"}'),
            ('duplicate', 'substation', 'B', 'Other source', 0, 51, 132, Point(0, 51).wkb, '{}'),
        ]
        db.executemany('INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?)', rows)
        db.executemany('INSERT INTO spatial_index VALUES (?,?,?,?,?)', [(1, 0, 0, 51, 51), (2, -1, 2, 51, 51), (4, 0, 0, 51, 51)])
        db.execute("INSERT INTO edges VALUES ('station','line','A','terminal_incidence','published')")
        db.execute("INSERT INTO edges VALUES ('line','unlocated','A','terminal_incidence','published')")
        db.execute("INSERT INTO relationships VALUES ('station','unlocated','A','contains','published')")
    app = Flask(__name__)
    app.config.update(TESTING=True, GRID_DATABASE=path)
    app.register_blueprint(bp)
    return app.test_client()


def test_bbox_includes_crossing_route_and_paginates_without_duplicates(client):
    query = dict(bbox='-.1,50.9,.1,51.1', source='A', limit=1)
    first = client.get('/api/grid/assets', query_string=query).get_json()
    assert first['features'][0]['id'] == 'station'
    assert first['truncated'] is True
    query['after'] = first['next_after']
    second = client.get('/api/grid/assets', query_string=query).get_json()
    assert second['features'][0]['id'] == 'line'
    assert second['next_after'] is None
    assert second['features'][0]['properties']['voltage_kv'] is None


def test_display_category_filter_and_source_isolation(client):
    data = client.get('/api/grid/assets?source=A&categories=substation').get_json()
    assert [f['id'] for f in data['features']] == ['station']
    assert data['features'][0]['properties']['category'] == 'substation'


@pytest.mark.parametrize('query', ['bbox=nan,0,1,2', 'bbox=1,0,0,2', 'bbox=0,0,1',
    'limit=3001', 'limit=0', 'after=-1', 'categories=invalid', 'bbox=0,-91,1,2'])
def test_invalid_queries_fail_explicitly(client, query):
    assert client.get('/api/grid/assets?' + query).status_code == 400


def test_unlocated_records_and_unknown_prediction_are_preserved(client):
    data = client.get('/api/grid/search?id=unlocated').get_json()['features'][0]
    assert data['geometry'] is None
    assert data['properties']['failure_probability'] is None
    assert data['properties']['source_properties'] == {'rating': 'unknown'}


def test_relationships_stay_separate_and_traverse_both_directions(client):
    data = client.get('/api/grid/asset?id=station').get_json()
    assert [c['neighbor_id'] for c in data['connections']] == ['line']
    membership = client.get('/api/grid/asset?id=station&relation=relationships').get_json()
    assert [c['neighbor_id'] for c in membership['connections']] == ['unlocated']
    incoming = client.get('/api/grid/asset?id=unlocated').get_json()
    assert incoming['connections'][0]['neighbor_id'] == 'line'
    assert client.get('/api/grid/asset?id=station&relation=candidate_edges').get_json()['connections'] == []


def test_id_and_table_parameters_cannot_inject_sql(client):
    assert client.get('/api/grid/asset', query_string={'id': "' OR 1=1 --"}).status_code == 404
    assert client.get('/api/grid/asset?id=station&relation=nodes').status_code == 400
    assert client.get('/api/grid/asset?id=station&offset=-1').status_code == 400
    assert client.get('/api/grid/assets', query_string={'source': "A' OR 1=1 --"}).get_json()['features'] == []


def test_unavailable_database_does_not_create_empty_database(client, tmp_path):
    missing = tmp_path / 'missing.sqlite'
    client.application.config['GRID_DATABASE'] = missing
    assert client.get('/api/grid/assets').status_code == 503
    assert not missing.exists()
