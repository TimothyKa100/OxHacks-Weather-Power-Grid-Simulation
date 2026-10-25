"""Flask JSON API, adapted from ICHack26's app.py architecture (see ATTRIBUTION.md)."""
from functools import lru_cache
from pathlib import Path
import json
import math
from threading import Lock
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import BadRequest, UnsupportedMediaType
from .simulation import NETWORKS, Network, run_experiment
from .weather import validate_weather, STORM_PRESETS
from .decisions import validate_economics
from .storm import run_storm
from .benchmark import STORM_LIST, STORMS, run_real_storm
from .planning import run_storm_plan

ROOT=Path(__file__).resolve().parents[1]
app=Flask(__name__,static_folder=str(ROOT/'frontend/dist'),static_url_path='')
app.config['MAX_CONTENT_LENGTH']=4*1024*1024  # bounded 72-hour GB weather/probability imports
nets={name:Network(name) for name in NETWORKS}
experiment_lock=Lock()

@lru_cache(maxsize=12)
def cached_experiment(network,request_json):
    # Keyed on the full validated request, so imported weather and plans are cached too.
    return run_experiment(nets[network],**json.loads(request_json))

@lru_cache(maxsize=6)
def network_named(name):
    """'gb' (the backbone) or a view network built by /api/view-network."""
    if name in nets: return nets[name]
    from .view_network import path_for
    if not name.startswith('view:') or not path_for(name).exists(): raise ValueError('Unknown network; request /api/view-network first')
    return Network(name)

@lru_cache(maxsize=4)
def cached_storm_plan(request_json):
    params=json.loads(request_json)
    return run_storm_plan(network_named(params.pop('network')),**params)

@app.post('/api/storm-plan')
def storm_plan():
    try:
        data=request.get_json()
        if not isinstance(data,dict): raise ValueError('Expected a JSON object')
        scenario=data.get('scenario','synthetic')
        weather=data.get('weather')
        name=data.get('network','gb')
        if not isinstance(name,str): raise ValueError('network must be a name')
        net=network_named(name)
        if weather is not None and name!='gb': raise ValueError('Imported weather is defined on the gb network branch order')
        if not isinstance(scenario,str) or (scenario not in ('synthetic','imported') and scenario not in STORMS):
            raise ValueError('Unknown storm scenario')
        if (scenario=='imported')!=(weather is not None): raise ValueError('Imported scenario requires a weather profile; other scenarios use their own weather')
        if weather is not None:
            validate_weather(weather,net.m)
            if 'branch_ids' in weather and weather['branch_ids'] != [e['id'] for e in net.branches]:
                raise ValueError('Weather branch_ids must match the GB network branch order')
        def integer(key,default,low,high):
            value=data.get(key,default)
            if isinstance(value,bool) or not isinstance(value,int) or not low<=value<=high:
                raise ValueError(f'{key} must be an integer between {low} and {high}')
            return value
        params={key:integer(key,*bounds) for key,bounds in {
            'peak_gust':(160,60,250),'hours':(36,1 if weather is not None else 6,72),'seed':(1,0,2147483647),
            'samples':(16,16,128),'budget':(5,0,12),'crews':(6,1,40)}.items()}
        preset=data.get('storm_type','single_peak')
        if not isinstance(preset,str) or preset not in STORM_PRESETS: raise ValueError('Unknown storm preset')
        economics=validate_economics(data.get('economics'))
        ids={e['id']:i for i,e in enumerate(net.branches)}
        if set(economics['asset_costs_gbp'])-set(ids): raise ValueError('Unknown asset cost IDs')
        custom=data.get('custom_lines')
        if custom is not None:
            if not isinstance(custom,list) or any(not isinstance(i,str) or i not in ids for i in custom):
                raise ValueError('custom_lines must contain GB branch IDs')
            if len(set(custom))!=len(custom): raise ValueError('Duplicate custom line IDs')
            if sum(net.cost[ids[i]] for i in custom)>params['budget']: raise ValueError('Custom plan exceeds budget')
        params.update(scenario=scenario,storm_type=preset,economics=economics,custom_lines=custom,weather=weather,network=name)
    except (ValueError,BadRequest,UnsupportedMediaType) as exc:
        return jsonify({'error':str(exc)}),400
    if not experiment_lock.acquire(blocking=False):
        return jsonify({'error':'An experiment is running. Try again shortly.'}),429
    try:
        return jsonify(cached_storm_plan(json.dumps(params,sort_keys=True)))
    finally:
        experiment_lock.release()

@app.get('/api/health')
def health(): return jsonify({'status':'ok','networks':list(NETWORKS),'model':'DC','weather':'synthetic'})

@app.get('/api/network')
def network():
    name=request.args.get('name','ieee14')
    if name not in nets: return jsonify({'error':f'network must be one of {", ".join(NETWORKS)}'}),400
    return jsonify(nets[name].payload())

@app.get('/api/storms')
def storms():
    """Real storms available to /api/storm (built by scripts/build_storm_benchmark.py), plus the grid they run on."""
    fields=('id','name','start','hours','operators','outages','peak_gust_kmh')
    return jsonify({'storms':[{k:s.get(k) for k in fields} for s in STORM_LIST],'network':nets['gb'].payload()})

@app.get('/api/assets')
def assets():
    """Most important archive assets in a view (map units), for the procedural level of detail."""
    from .assets import load, select, payload
    try:
        box=[float(request.args[k]) for k in ('x0','y0','x1','y1')]
        budget=int(request.args.get('limit',1000))
        if not 1<=budget<=3000 or not all(map(math.isfinite,box)) or box[2]<box[0] or box[3]<box[1]: raise ValueError()
    except (KeyError,ValueError):
        return jsonify({'error':'Give x0, y0, x1, y1 in map units (x1>=x0, y1>=y0) and limit 1-3000'}),400
    try: net=network_named(request.args.get('network','gb'))
    except ValueError as exc: return jsonify({'error':str(exc)}),400
    index=load(net)
    if index is None:
        return jsonify({'total_in_view':0,'shown':0,'assets':[],'note':'Run scripts/build_asset_index.py to index the grid archive'})
    return jsonify(payload(index,*select(index,*box,budget=budget)))

@app.get('/api/view-network')
def view_network():
    """Simulation network for a map view: the national 33-400 kV graph reduced to this view."""
    from .view_network import ensure
    try:
        box=[float(request.args[k]) for k in ('x0','y0','x1','y1')]
        if not all(map(math.isfinite,box)) or box[2]<=box[0] or box[3]<=box[1]: raise ValueError()
    except (KeyError,ValueError):
        return jsonify({'error':'Give x0, y0, x1, y1 in map units (x1>x0, y1>y0)'}),400
    name=ensure(box)
    if name is None:
        return jsonify({'error':'Run scripts/build_asset_index.py and scripts/build_detail_graph.py to build the detailed graph'}),404
    net=network_named(name)
    return jsonify({**net.payload(),'id':name})

RISK_DIR=ROOT/'data/failure_model/exports'

def corridor_risk_files():
    """Exports from `python -m failure_model gb-export` (area-level corridor risk), keyed by file stem."""
    files={}
    for path in sorted(RISK_DIR.glob('*.json')):
        try: profile=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError): continue
        if isinstance(profile,dict) and profile.get('prediction_unit')=='line_corridor_area_risk':
            files[path.stem]=profile
    return files

@app.get('/api/corridor-risk')
def corridor_risk():
    """A display layer only: these probabilities never enter the line-failure simulation."""
    files=corridor_risk_files()
    layer=request.args.get('id')
    if layer is None:
        return jsonify({'layers':[{'id':k,'name':p.get('name') or k,'start':p['hours'][0]['timestamp'],'hours':len(p['hours']),
                                   'model_version':p['prediction']['model_version']} for k,p in files.items()]})
    if layer not in files: return jsonify({'error':'Unknown corridor-risk layer'}),404
    p=files[layer]
    if p.get('branch_ids')!=[e['id'] for e in nets['gb'].branches]:
        return jsonify({'error':'Layer branch order does not match the GB network; re-export it'}),409
    return jsonify({'id':layer,'name':p.get('name') or layer,'source':p['source'],'prediction':p['prediction'],
                    'timestamps':[h['timestamp'] for h in p['hours']],
                    'probability':[h['failure_probability'] for h in p['hours']]})

@app.post('/api/storm')
def storm():
    data=request.get_json(silent=True)
    if not isinstance(data,dict): return jsonify({'error':'Expected a JSON object'}),400
    scenario=data.get('scenario','synthetic')
    if scenario!='synthetic':
        if scenario not in STORMS: return jsonify({'error':f'scenario must be synthetic or one of {", ".join(STORMS)}'}),400
        seed=data.get('seed',1)
        if isinstance(seed,bool) or not isinstance(seed,int) or not 0<=seed<=2147483647:
            return jsonify({'error':'seed must be an integer between 0 and 2147483647'}),400
        return jsonify({**run_real_storm(nets['gb'],scenario,seed),'network':nets['gb'].payload()})
    limits={'peak_gust':(160,60,250),'hours':(36,6,72),'seed':(1,0,2147483647)}
    params={}
    for key,(default,low,high) in limits.items():
        value=data.get(key,default)
        if isinstance(value,bool) or not isinstance(value,int) or not low<=value<=high:
            return jsonify({'error':f'{key} must be an integer between {low} and {high}'}),400
        params[key]=value
    return jsonify({**run_storm(nets['gb'],**params),'network':nets['gb'].payload()})

@app.post('/api/experiment')
def experiment():
    try:
        data=request.get_json()
        if not isinstance(data,dict): raise ValueError('Expected a JSON object')
        def integer(key,default,low,high):
            value=data.get(key,default)
            if isinstance(value,bool) or not isinstance(value,int) or not low<=value<=high:
                raise ValueError(f'{key} must be an integer between {low} and {high}')
            return value
        name=data.get('network','ieee14')
        if name not in nets: raise ValueError(f'network must be one of {", ".join(NETWORKS)}')
        net=nets[name]
        mode=data.get('mode','legacy')
        if mode not in ('legacy','multiday'): raise ValueError('mode must be legacy or multiday')
        weather=data.get('weather')
        if weather is not None:
            if mode!='multiday': raise ValueError('Imported weather requires multiday mode')
            validate_weather(weather,net.m)
            if 'branch_ids' in weather and weather['branch_ids'] != [e['id'] for e in net.branches]:
                raise ValueError('Weather branch_ids must match /api/network branch order')
        storm_type=data.get('storm_type','single_peak')
        if not isinstance(storm_type,str) or storm_type not in STORM_PRESETS: raise ValueError('Unknown storm preset')
        economics=validate_economics(data.get('economics'))
        custom=data.get('custom_lines')
        ids={e['id']:i for i,e in enumerate(net.branches)}
        if set(economics['asset_costs_gbp'])-set(ids): raise ValueError('Unknown asset cost IDs')
        if custom is not None:
            if not isinstance(custom,list) or any(not isinstance(i,str) or i not in ids for i in custom):
                raise ValueError('custom_lines must contain known branch IDs')
            if len(set(custom))!=len(custom): raise ValueError('Duplicate custom line IDs')
        params=dict(intensity=integer('intensity',100,30,140),seed=integer('seed',26,0,2147483647),
                    samples=integer('samples',128,16,512),budget=integer('budget',5,0,12),mode=mode,
                    hours=integer('hours',48,1,72),crews=integer('crews',2,1,40),weather=weather,
                    storm_type=storm_type,custom_lines=custom,economics=economics)
        if custom is not None and sum(net.cost[ids[i]] for i in custom)>params['budget']:
            raise ValueError('Custom plan exceeds the budget')
        if mode=='multiday' and params['samples']>128: raise ValueError('Multi-day mode supports up to 128 scenarios')
    except (ValueError,BadRequest,UnsupportedMediaType) as exc:
        return jsonify({'error':str(exc)}),400
    # Queue briefly: a repeated request then hits the cache instead of failing.
    if not experiment_lock.acquire(timeout=120):
        return jsonify({'error':'An experiment is running. Try again shortly.'}),429
    try:
        return jsonify(cached_experiment(name,json.dumps(params,sort_keys=True)))
    finally: experiment_lock.release()

@app.get('/')
def index():
    if not (ROOT/'frontend/dist/index.html').exists():
        return 'Build the frontend: cd frontend && npm install && npm run build',503
    return send_from_directory(app.static_folder,'index.html')

if __name__=='__main__':
    app.run(host='127.0.0.1',port=5001,debug=False,threaded=True)
