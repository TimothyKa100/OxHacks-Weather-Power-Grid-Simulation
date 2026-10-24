"""Flask JSON API, adapted from ICHack26's app.py architecture (see ATTRIBUTION.md)."""
from functools import lru_cache
from pathlib import Path
from threading import Lock
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import BadRequest, UnsupportedMediaType
from .simulation import Network, run_experiment
from .weather import validate_weather

ROOT=Path(__file__).resolve().parents[1]
app=Flask(__name__,static_folder=str(ROOT/'frontend/dist'),static_url_path='')
app.config['MAX_CONTENT_LENGTH']=262144
net=Network()
experiment_lock=Lock()

@lru_cache(maxsize=12)
def cached_experiment(intensity,seed,samples,budget,mode,hours,crews):
    return run_experiment(net,intensity,seed,samples,budget,mode,hours,crews)

@app.get('/api/health')
def health(): return jsonify({'status':'ok','benchmark':'IEEE 14-bus','model':'DC','weather':'synthetic'})

@app.get('/api/network')
def network(): return jsonify(net.payload())

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
        mode=data.get('mode','legacy')
        if mode not in ('legacy','multiday'): raise ValueError('mode must be legacy or multiday')
        weather=data.get('weather')
        if weather is not None:
            if mode!='multiday': raise ValueError('Imported weather requires multiday mode')
            validate_weather(weather,net.m)
        params=(integer('intensity',100,30,140),integer('seed',26,0,2147483647),integer('samples',128,16,512),integer('budget',5,0,12),mode,integer('hours',48,1,72),integer('crews',2,1,10))
        if mode=='multiday' and params[2]>128: raise ValueError('Multi-day mode supports up to 128 scenarios')
    except (ValueError,BadRequest,UnsupportedMediaType) as exc:
        return jsonify({'error':str(exc)}),400
    if not experiment_lock.acquire(blocking=False):
        return jsonify({'error':'An experiment is running. Try again shortly.'}),429
    try:
        return jsonify(run_experiment(net,*params,weather=weather) if weather is not None else cached_experiment(*params))
    finally: experiment_lock.release()

@app.get('/')
def index():
    if not (ROOT/'frontend/dist/index.html').exists():
        return 'Build the frontend: cd frontend && npm install && npm run build',503
    return send_from_directory(app.static_folder,'index.html')

if __name__=='__main__':
    app.run(host='127.0.0.1',port=5001,debug=False,threaded=True)
