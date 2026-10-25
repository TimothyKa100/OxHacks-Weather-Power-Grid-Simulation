"""Experimental, bounded ML residual correction of an engineering hourly hazard."""
from copy import deepcopy
from datetime import datetime, timezone
import math
import numpy as np

DEFAULTS = {'beta': .5, 'min_multiplier': .5, 'max_multiplier': 2.}
TARGETS = ('corridor_outage', 'transmission_weather_failure')


def settings(value=None):
    if value is not None and (not isinstance(value, dict) or set(value)-set(DEFAULTS)):
        raise ValueError('Unknown hybrid settings')
    out = {**DEFAULTS, **(value or {})}
    for key, value in out.items():
        if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value):
            raise ValueError(f'Invalid hybrid {key}')
    if not 0<=out['beta']<=2 or not .05<=out['min_multiplier']<=1 or not 1<=out['max_multiplier']<=10:
        raise ValueError('Require beta 0–2, min_multiplier 0.05–1, max_multiplier 1–10')
    return out


def instant(value):
    try:
        t = datetime.fromisoformat(value.replace('Z','+00:00'))
        if t.tzinfo is None: raise ValueError()
        return t.astimezone(timezone.utc)
    except (ValueError,AttributeError,TypeError):
        raise ValueError('Hybrid timestamps must include a timezone')


def validate_pair(profile, branch_ids, timestamps=None):
    if not isinstance(profile,dict) or profile.get('prediction_unit')!='paired_risk_modifier':
        raise ValueError('Hybrid mode requires a paired_risk_modifier profile, not raw corridor probabilities')
    if not isinstance(profile.get('source'),str) or not profile['source'].strip(): raise ValueError('Pair source required')
    if profile.get('branch_ids')!=list(branch_ids): raise ValueError('Hybrid branch order must match the network')
    meta=profile.get('prediction',{})
    if not isinstance(meta,dict) or any(not isinstance(meta.get(k),str) or not meta[k].strip() for k in
       ('model_version','reference_model_version','target','training_cutoff','issued_at','evidence_status')):
        raise ValueError('Pair model/reference provenance, target and evidence_status are required')
    if meta['target'] not in TARGETS or meta['evidence_status'] not in ('observed_labels','assumed_target','synthetic_fixture'):
        raise ValueError('Unknown pair target or evidence status')
    cutoff, issued=instant(meta['training_cutoff']),instant(meta['issued_at'])
    rows=profile.get('hours')
    if not isinstance(rows,list) or not 1<=len(rows)<=72: raise ValueError('Pair requires 1–72 hours')
    dates=[]
    for row in rows:
        if not isinstance(row,dict): raise ValueError('Invalid paired hour')
        dates.append(instant(row.get('timestamp')))
        for key in ('full_probability','reference_probability'):
            try: values=np.asarray(row[key],dtype=float)
            except (KeyError,ValueError,TypeError): raise ValueError(f'Invalid {key}')
            if values.shape!=(len(branch_ids),) or not np.all(np.isfinite(values)) or np.any((values<0)|(values>1)):
                raise ValueError(f'{key} requires one finite probability in [0,1] per branch')
    if cutoff>issued or issued>dates[0]: raise ValueError('Pair must be fitted and issued before replay starts')
    if any((b-a).total_seconds()!=3600 for a,b in zip(dates,dates[1:])): raise ValueError('Pair hours must be consecutive')
    if timestamps is not None and dates!=[instant(t) for t in timestamps]: raise ValueError('Pair hours must exactly match storm exposure hours')
    return profile


def combine(engineering, full, reference, config=None):
    """Stable endpoints: zero engineering risk remains zero; unit risk remains one."""
    config=settings(config)
    p,q,r=np.broadcast_arrays(np.asarray(engineering,float),np.asarray(full,float),np.asarray(reference,float))
    if any(np.any(~np.isfinite(a)|(a<0)|(a>1)) for a in (p,q,r)): raise ValueError('Invalid probability')
    logit=lambda x: np.log(np.clip(x,1e-12,1-1e-12))-np.log1p(-np.clip(x,1e-12,1-1e-12))
    log_multiplier=np.clip(config['beta']*(logit(q)-logit(r)),np.log(config['min_multiplier']),np.log(config['max_multiplier']))
    multiplier=np.exp(log_multiplier)
    with np.errstate(divide='ignore'):
        result=-np.expm1(np.log1p(-p)*multiplier)
    return result,multiplier


def redistribute(engineering, full, reference, supported, config=None):
    """Bounded relative ML tilt preserving sum of one-hour line probabilities.

    Only supported lines participate; all others retain their physical hazard.
    This preserves an assumed engineering rate, not a measured failure count.
    """
    config=settings(config)
    p=np.asarray(engineering,float)
    _,raw=combine(p,full,reference,config)
    eligible=np.asarray(supported,bool) & (p>0) & (p<1)
    multiplier=np.ones_like(p)
    if np.count_nonzero(eligible)>1 and config['beta']>0:
        hazard=-np.log1p(-p[eligible]); target=p[eligible].sum()
        r=raw[eligible]
        low=config['min_multiplier']/r.max()
        high=config['max_multiplier']/r.min()
        for _ in range(80):
            scale=(low+high)/2
            m=np.clip(r*scale,config['min_multiplier'],config['max_multiplier'])
            if (-np.expm1(-hazard*m)).sum()<target: low=scale
            else: high=scale
        multiplier[eligible]=np.clip(r*((low+high)/2),config['min_multiplier'],config['max_multiplier'])
    with np.errstate(divide='ignore'):
        final=-np.expm1(np.log1p(-p)*multiplier)
    return np.where(multiplier==1,p,final),multiplier


def hybrid_weather(exposure, pair, config=None):
    ids=exposure['branch_ids']; dates=[r['timestamp'] for r in exposure['hours']]
    validate_pair(pair,ids,dates); config=settings(config)
    result=deepcopy(exposure)
    result.pop('prediction',None)
    result['engineering_model']={'name':'hybrid-hazard-v1','parameters':config}
    relative=pair['prediction'].get('integration_method')=='relative-risk-budget-v1'
    result['hybrid_provenance']={'prediction':deepcopy(pair['prediction']),'source':pair['source'],
        'interpretation':'Experimental cross-target correction; not calibrated transmission reliability' if pair['prediction']['target']=='corridor_outage'
                         else 'Experimental correction under a declared transmission target; efficacy and beta require validation'}
    if relative:
        result['hybrid_provenance']['interpretation']='Engineering failure rate with bounded, weather-matched relative corridor ML risk; hourly expected line failures preserved; transmission transfer unvalidated'
    for row,ml in zip(result['hours'],pair['hours']):
        engineering=np.asarray(row['failure_probability'])
        if relative:
            counts=np.asarray(ml.get('reference_peer_count',[]))
            domain=np.asarray(pair['prediction'].get('in_training_domain',[]),bool)
            if counts.shape!=engineering.shape or domain.shape!=engineering.shape:
                raise ValueError('Relative hybrid requires peer support and training-domain flags')
            final,multiplier=redistribute(engineering,ml['full_probability'],ml['reference_probability'],counts>=5,config)
        else:
            final,multiplier=combine(engineering,ml['full_probability'],ml['reference_probability'],config)
        row['failure_probability']=final.tolist()
        if 'failure_probability_hardened' in row:
            # Hardened towers get the same ML tilt as the line itself (raised-fragility probability x multiplier).
            hard=np.asarray(row['failure_probability_hardened'],float)
            with np.errstate(divide='ignore'):
                row['failure_probability_hardened']=np.where(multiplier==1,hard,-np.expm1(np.log1p(-np.minimum(hard,1-1e-12))*multiplier)).tolist()
        row['hybrid_diagnostics']={'engineering_probability':engineering.tolist(),
            'ml_full_probability':ml['full_probability'],'ml_reference_probability':ml['reference_probability'],
            'hazard_multiplier':multiplier.tolist(),'unreinforced_probability':final.tolist()}
        if relative:
            row['hybrid_diagnostics'].update(reference_peer_count=ml['reference_peer_count'],
                in_training_domain=pair['prediction']['in_training_domain'],
                expected_engineering_failures=float(engineering.sum()),expected_hybrid_failures=float(final.sum()))
    result['source']='Experimental hybrid: '+exposure['source']+'; '+pair['source']
    return result


def fragility_settings(value=None):
    if value is not None and (not isinstance(value,dict) or set(value)-{'median_gust','dispersion'}):
        raise ValueError('Unknown fragility settings')
    result={'median_gust':265.,'dispersion':.12,**(value or {})}
    for k,v in result.items():
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v): raise ValueError('Invalid fragility setting')
    if not 150<=result['median_gust']<=500 or not .02<=result['dispersion']<=1:
        raise ValueError('Require median gust 150–500 km/h and dispersion 0.02–1')
    return result
