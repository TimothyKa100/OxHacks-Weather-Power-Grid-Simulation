"""Train/export paired scores for the declared target; no raw data is manufactured.

python -m failure_model.paired train rows.parquet contract.json model-directory
python -m failure_model.paired export features.parquet model-directory paired.json
"""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.special import expit
from .common import write_json,sha256
from .gb_lines import FEATURES
from .reference import fit as fit_reference, predict as reference_predict, calibrator, REFERENCE_FEATURES
from .evaluation import metrics
from backend.hybrid import TARGETS, validate_pair


def validate_rows(rows, contract):
    features=contract.get('features',FEATURES)
    if not isinstance(features,list) or len(set(features))!=len(features) or not set(REFERENCE_FEATURES)<=set(features):
        raise ValueError('Ordered features must include the weather-aware reference features')
    # Future columns must be explicitly reviewed rather than silently admitted.
    if set(features)-set(FEATURES+['vegetation_fraction','age_years','condition_score','maintenance_age_days']):
        raise ValueError('Unreviewed predictor names in feature contract')
    if contract.get('target') not in TARGETS or contract.get('evidence_status') not in ('observed_labels','assumed_target','synthetic_fixture'):
        raise ValueError('Declare target and evidence status')
    if not isinstance(contract.get('source'),str) or not contract['source'].strip(): raise ValueError('Label source required')
    required={'asset_id','timestamp_utc','storm_id','split','failed_next_hour',*features}
    if not required<=set(rows): raise ValueError('Missing feature/identity/label/split columns')
    rows=rows.copy(); rows['timestamp_utc']=pd.to_datetime(rows.timestamp_utc,utc=True,errors='raise')
    if rows[list(required)].drop(columns=features).isna().any().any(): raise ValueError('Missing identity or label')
    if rows.duplicated(['asset_id','timestamp_utc']).any(): raise ValueError('Duplicate asset-hour')
    if not rows.failed_next_hour.isin([0,1]).all(): raise ValueError('Binary labels required')
    if np.isinf(rows[features].to_numpy(dtype=float)).any(): raise ValueError('Infinite predictor values')
    if contract['target']=='transmission_weather_failure':
        for flag in ('operational_at_start','reporting_complete'):
            if flag not in rows or not rows[flag].eq(True).all(): raise ValueError(f'Transmission target requires {flag}')
    if contract['target']=='transmission_weather_failure' and contract['evidence_status']=='observed_labels':
        for flag in ('asset_identity_verified','label_verified'):
            if flag not in rows or not rows[flag].eq(True).all(): raise ValueError(f'Observed transmission evidence requires {flag}')
    names=['fit','tune','calibrate','test']
    if set(rows.split)!=set(names): raise ValueError('Provide exactly fit, tune, calibrate, test partitions')
    parts={name:rows[rows.split.eq(name)] for name in names}
    for a,b in zip(names,names[1:]):
        if parts[b].timestamp_utc.min()-parts[a].timestamp_utc.max()<pd.Timedelta(hours=25):
            raise ValueError('Partitions require a 25-hour minimum boundary gap')
    for i,a in enumerate(names):
        if parts[a].failed_next_hour.nunique()!=2: raise ValueError(f'{a} requires both outcomes for this experiment')
        for b in names[i+1:]:
            shared=set(parts[a].storm_id)&set(parts[b].storm_id)
            if shared: raise ValueError('Storm/group IDs must not cross partitions (use distinct quiet-period IDs too)')
    return rows,parts,features


def train(rows,contract,destination,seed=26):
    from xgboost import XGBClassifier
    rows,parts,features=validate_rows(rows,contract)
    destination=Path(destination)
    if destination.exists() and any(destination.iterdir()): raise ValueError('Use a new immutable model directory')
    wind=set(REFERENCE_FEATURES[:4])
    model=XGBClassifier(objective='binary:logistic',tree_method='hist',max_depth=3,n_estimators=300,
        learning_rate=.05,min_child_weight=5,n_jobs=2,random_state=seed,eval_metric='logloss',early_stopping_rounds=20,
        monotone_constraints='('+','.join('1' if f in wind else '0' for f in features)+')')
    model.fit(parts['fit'][features],parts['fit'].failed_next_hour,
              eval_set=[(parts['tune'][features],parts['tune'].failed_next_hour)],verbose=False)
    ca=parts['calibrate']; calibration=calibrator(model.predict(ca[features],output_margin=True),ca.failed_next_hour)
    reference=fit_reference(parts['fit'],ca)
    test=parts['test']; full=expit(calibration['coefficient']*model.predict(test[features],output_margin=True)+calibration['intercept'])
    report={'full_model':metrics(test.failed_next_hour,full),
            'weather_reference':metrics(test.failed_next_hour,reference_predict(test,reference)),
            'evidence_status':contract['evidence_status'],'target':contract['target'],
            'scope':'Label-conditional held-out scores. Synthetic or assumed-target scores are plumbing tests, not empirical reliability.'}
    destination.mkdir(parents=True,exist_ok=True)
    model.save_model(destination/'model.ubj'); write_json(destination/'calibration.json',calibration)
    write_json(destination/'reference_model.json',reference); write_json(destination/'evaluation.json',report)
    manifest={**contract,'features':features,'model_version':destination.name,'reference_model_version':destination.name+'-weather-reference',
        'training_cutoff':ca.timestamp_utc.max().isoformat(),'seed':seed,
        'sha256':{f:sha256(destination/f) for f in ('model.ubj','calibration.json','reference_model.json')},
        'partitions':{k:{'rows':len(v),'events':int(v.failed_next_hour.sum()),'start':v.timestamp_utc.min().isoformat(),
                         'end':v.timestamp_utc.max().isoformat()} for k,v in parts.items()}}
    write_json(destination/'model_manifest.json',manifest)
    return report


def export(features,directory,destination,network_path=None):
    from xgboost import XGBClassifier
    directory=Path(directory); m=json.loads((directory/'model_manifest.json').read_text())
    for file,digest in m['sha256'].items():
        if sha256(directory/file)!=digest: raise ValueError(f'Artifact checksum mismatch: {file}')
    model=XGBClassifier(); model.load_model(directory/'model.ubj')
    calibration=json.loads((directory/'calibration.json').read_text()); ref=json.loads((directory/'reference_model.json').read_text())
    features=features.copy(); features['timestamp_utc']=pd.to_datetime(features.timestamp_utc,utc=True)
    if features.duplicated(['asset_id','timestamp_utc']).any(): raise ValueError('Duplicate inference asset-hour')
    network_path=network_path or Path(__file__).resolve().parents[1]/'data/gb_network.json'
    ids=[e['id'] for e in json.loads(Path(network_path).read_text())['branches']]
    if set(features.asset_id)!=set(ids): raise ValueError('Inference rows must cover exactly the network branches')
    features['full_probability']=expit(calibration['coefficient']*model.predict(features[m['features']],output_margin=True)+calibration['intercept'])
    features['reference_probability']=reference_predict(features,ref)
    rows=[]
    for stamp,group in features.groupby('timestamp_utc',sort=True):
        if set(group.asset_id)!=set(ids): raise ValueError('Incomplete inference hour')
        group=group.set_index('asset_id').loc[ids]
        rows.append({'timestamp':stamp.isoformat(),'full_probability':group.full_probability.tolist(),
                     'reference_probability':group.reference_probability.tolist()})
    profile={'prediction_unit':'paired_risk_modifier','source':m['source'],'branch_ids':ids,
        'prediction':{k:m[k] for k in ('model_version','reference_model_version','target','training_cutoff','evidence_status')},'hours':rows}
    profile['prediction']['issued_at']=rows[0]['timestamp']
    profile['prediction']['reference_features']=ref['features']
    profile['prediction']['features']=m['features']
    profile['prediction']['information_mode']='retrospective feature replay; issue time here is the scenario cutoff, not an archived forecast'
    validate_pair(profile,ids)
    write_json(destination,profile)
    return profile


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='command',required=True)
    t=sub.add_parser('train'); t.add_argument('rows'); t.add_argument('contract'); t.add_argument('destination')
    e=sub.add_parser('export'); e.add_argument('features'); e.add_argument('directory'); e.add_argument('destination');e.add_argument('--network')
    args=p.parse_args()
    try:
        if args.command=='train': result=train(pd.read_parquet(args.rows),json.loads(Path(args.contract).read_text()),args.destination)
        else:
            result=export(pd.read_parquet(args.features),args.directory,args.destination,args.network)
            result={'destination':args.destination,'hours':len(result['hours'])}
        print(json.dumps(result,indent=2,allow_nan=False))
    except (ValueError,KeyError) as exc: p.error(str(exc))
