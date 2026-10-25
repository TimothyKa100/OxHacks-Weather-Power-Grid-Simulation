"""Read-only, spatially paginated access to the source-preserving national graph."""
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path
from flask import Blueprint, current_app, jsonify, request
from shapely import from_wkb, to_geojson
from .asset_rules import CATEGORIES, TYPE_CATEGORY, display_name

bp = Blueprint('grid', __name__, url_prefix='/api/grid')
ROOT = Path(__file__).resolve().parents[1]


def connect():
    cleaned = ROOT / 'data/grid/uk_power_grid.sqlite'
    path = Path(current_app.config.get('GRID_DATABASE', cleaned if cleaned.exists() else ROOT / 'data/grid/british_grid.sqlite')).resolve()
    db = sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


@bp.errorhandler(sqlite3.OperationalError)
def unavailable(error):
    current_app.logger.warning('Grid database unavailable: %s', error)
    return jsonify(error='Grid database unavailable. See docs/NETWORK_MAP.md for setup.'), 503


def feature(row, detail=False):
    values = dict(row)
    geometry = values.pop('geometry_wgs84_wkb', None)
    if geometry:
        shape = from_wkb(geometry)
        if not detail:
            shape = shape.simplify(0.0002, preserve_topology=True)
        geometry = json.loads(to_geojson(shape))
    elif values.get('longitude') is not None and values.get('latitude') is not None:
        geometry = {'type': 'Point', 'coordinates': [values['longitude'], values['latitude']]}
    values.pop('rowid', None)
    values['category'] = TYPE_CATEGORY.get(values['node_type'])
    values['display_name'] = display_name(values['node_type'], values.get('name'))
    if detail:
        values['source_properties'] = json.loads(values.pop('properties_json') or '{}')
        values['failure_probability'] = None
        values['prediction_status'] = 'No trained model or verified component-failure labels'
    return {'type': 'Feature', 'id': values['node_id'], 'geometry': geometry, 'properties': values}


@bp.get('/summary')
def summary():
    with closing(connect()) as db:
        sources = [r[0] for r in db.execute('SELECT DISTINCT source FROM nodes')]
        metadata = {r[0]: json.loads(r[1]) for r in db.execute('SELECT key,value FROM metadata')}
        bounds=None
        if metadata.get('cleaning'):
            extent=db.execute('SELECT min(cell_x),max(cell_x),min(cell_y),max(cell_y) FROM node_cells').fetchone()
            if extent[0] is not None:bounds=[extent[0]/20-180,extent[2]/20-90,(extent[1]+1)/20-180,(extent[3]+1)/20-90]
    report_path = ROOT / 'data/grid/reports/coverage.json'
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    cleaning = metadata.get('cleaning')
    counts = {**report.get('counts', {})}
    counts.update(metadata.get('uk_refinement',{}).get('counts',{}))
    if cleaning: counts['nodes'] = cleaning['retained_nodes']
    return jsonify(categories=CATEGORIES, sources=sources, metadata=metadata,
                   counts=counts, located_nodes=cleaning['located_nodes'] if cleaning else report.get('located_nodes'),
                   cleaning=cleaning,bounds=bounds,coverage=metadata.get('coverage','Published and mapped grid source records; sources overlap.'))


@bp.get('/assets')
def assets():
    try:
        bbox = [float(x) for x in request.args.get('bbox', '-9,49,3,61').split(',')]
        if len(bbox) != 4 or not all(math.isfinite(x) for x in bbox):
            raise ValueError('bbox must contain four finite coordinates')
        west, south, east, north = bbox
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError('Invalid bounding box')
        limit = int(request.args.get('limit', 1500))
        after = int(request.args.get('after', 0))
        if not 1 <= limit <= 3000 or after < 0:
            raise ValueError('limit must be 1–3000; after must be nonnegative')
        groups = request.args.get('categories', ','.join(CATEGORIES)).split(',')
        if any(g not in CATEGORIES for g in groups):
            raise ValueError('Unknown asset category')
    except ValueError as error:
        return jsonify(error=str(error)), 400
    types = [t for g in groups for t in CATEGORIES[g]['types']]
    clauses = ['s.min_lon<=?', 's.max_lon>=?', 's.min_lat<=?', 's.max_lat>=?', 'n.rowid>?',
               'n.node_type IN (' + ','.join('?' for _ in types) + ')']
    params = [east, west, north, south, after, *types]
    source = request.args.get('source')
    if source:
        clauses.append('n.source=?')
        params.append(source)
    # Keep the spatial index outermost: SQLite otherwise scans every source record
    # before checking a small viewport. CROSS JOIN deliberately fixes this join order.
    sql = '''SELECT n.rowid,n.node_id,n.node_type,n.source,n.name,n.longitude,n.latitude,
             n.voltage_kv,n.geometry_wgs84_wkb FROM spatial_index s
             CROSS JOIN nodes n ON n.rowid=s.id WHERE ''' + ' AND '.join(clauses)
    with closing(connect()) as db:
        rows = db.execute(sql + ' ORDER BY n.rowid LIMIT ?', [*params, limit + 1]).fetchall()
    return jsonify(type='FeatureCollection', features=[feature(r) for r in rows[:limit]],
                   next_after=rows[limit-1]['rowid'] if len(rows) > limit else None,
                   truncated=len(rows) > limit)


@bp.get('/asset')
def asset():
    asset_id = request.args.get('id', '')
    try:
        offset = int(request.args.get('offset', 0))
        if offset < 0:
            raise ValueError()
    except ValueError:
        return jsonify(error='offset must be nonnegative'), 400
    kind = request.args.get('relation', 'edges')
    if kind not in ('edges', 'relationships', 'candidate_edges'):
        return jsonify(error='Unknown relation type'), 400
    with closing(connect()) as db:
        row = db.execute('SELECT * FROM nodes WHERE node_id=?', (asset_id,)).fetchone()
        if row is None:
            return jsonify(error='Asset not found'), 404
        if row['node_type'] not in TYPE_CATEGORY:
            return jsonify(error='Record is not a retained electricity-grid component'),404
        links = db.execute(f'''SELECT e.*, n.node_id AS neighbor_id,n.name AS neighbor_name,
                   n.node_type AS neighbor_type,n.latitude,n.longitude
                   FROM {kind} e JOIN nodes n ON n.node_id=CASE WHEN e.from_node=?
                   THEN e.to_node ELSE e.from_node END
                   WHERE e.from_node=? OR e.to_node=? LIMIT 201 OFFSET ?''',
                   (asset_id, asset_id, asset_id, offset)).fetchall()
    result = feature(row, detail=True)
    result['connections'] = [dict(r) for r in links[:200]]
    result['next_offset'] = offset + 200 if len(links) > 200 else None
    result['relation_table'] = kind
    return jsonify(result)


@bp.get('/search')
def search():
    # Exact source-scoped ID lookup also reaches records with no mapped coordinates.
    asset_id = request.args.get('id', '')
    with closing(connect()) as db:
        row = db.execute('SELECT * FROM nodes WHERE node_id=?', (asset_id,)).fetchone()
    return jsonify(features=[feature(row, detail=True)] if row and row['node_type'] in TYPE_CATEGORY else [])
