"""GB map adapter: one storm-plan chain from weather to a recommended mitigation.

Two outputs from the same weather:
- Local power-cut forecast (ML): the corridor model predicts where distribution faults are reported
  near each overhead line, scored against reported cuts for historical storms (backend/forecast.py).
- Transmission grid stress (physics): engineering route/pylon hazard -> Monte Carlo line failures ->
  cascade (redispatch, trips, islanding, supply loss) -> mitigations (reinforcement portfolios,
  prioritized repairs, both combined) on matched draws -> restoration with crews -> cost-benefit ->
  recommendation. The experimental hybrid (failure_mode='hybrid') can still tilt line hazards by the ML.
`chain` in the result reports each stage.
"""
from functools import lru_cache
import numpy as np
from .benchmark import STORMS, run_real_storm
from .storm import run_storm
from .simulation import run_experiment
from .hybrid import hybrid_weather, settings, fragility_settings
from .ml_risk import builtin_pair
from .forecast import power_cut_forecast


RECOVERY_HOURS = 48  # calm hours simulated after the storm so crews and repair priorities act


def map_frames(playback, context):
    """Keep one settled frame per hour; all cascade rounds remain in plan export."""
    settled={f['hour']:f for f in playback['frames']}
    backdrop={f['hour']:f for f in context['frames']}
    frames=[]
    for hour,frame in sorted(settled.items()):
        background=backdrop.get(hour,{})
        previous=frames[-1]['status'] if frames else frame['status']
        events=[{'type':status,'line':context['network']['branches'][i]['id']}
                for i,status in enumerate(frame['status']) if status!=previous[i]]
        frames.append({**frame,'events':events,'fallen_at':[None]*len(frame['status']),
                       'storm':background.get('storm'),'storm_radius':background.get('storm_radius',0.),
                       'line_failure_probability':frame['failure_probability'],
                       'islands':len(frame['islands'])})
    return frames


def run_storm_plan(net, scenario='synthetic', peak_gust=160, hours=36, seed=1,
                   storm_type='single_peak', samples=16, budget=5, crews=6,
                   custom_lines=None, economics=None, weather=None, failure_mode="engineering", hybrid_profile=None, hybrid=None, fragility=None,
                   recovery_hours=RECOVERY_HOURS):
    fragility=fragility_settings(fragility)
    imported=weather is not None
    context=({'network':net.payload(),'frames':[],'input_source':weather['source']} if imported
             else storm_context(net,scenario,peak_gust,hours,seed,storm_type,**fragility))
    exposures=weather if imported else context['planning_weather']
    baseline=None
    config=settings(hybrid)
    engineering=exposures
    if failure_mode not in ('engineering','hybrid'): raise ValueError('failure_mode must be engineering or hybrid')
    if failure_mode=='hybrid':
        pair=hybrid_profile if hybrid_profile is not None else builtin_pair(net,exposures,archive_storm=None if imported else scenario)
        baseline=run_experiment(net,seed=seed,samples=samples,budget=budget,mode='multiday',
            hours=len(exposures['hours']),crews=crews,weather=exposures,storm_type=storm_type,custom_lines=custom_lines,economics=economics,recovery_hours=recovery_hours)
        exposures=hybrid_weather(exposures,pair,config)
    plan=run_experiment(net,seed=seed,samples=samples,budget=budget,mode='multiday',
                        hours=len(exposures['hours']),crews=crews,weather=exposures,
                        storm_type=storm_type,custom_lines=custom_lines,economics=economics,recovery_hours=recovery_hours)
    descriptor=dict(scenario=scenario,peak_gust=peak_gust,hours=len(exposures['hours']),recovery_hours=recovery_hours,seed=seed,
                    storm_type=storm_type,samples=samples,budget=budget,crews=crews,
                    custom_lines=custom_lines,economics=plan['config']['economics'],weather=weather,
                    failure_mode=failure_mode,hybrid_profile=hybrid_profile,hybrid=config,fragility=fragility,network=net.name)
    plan['model']['weather']=exposures['source']
    if 'engineering_model' in exposures:
        plan['model']['failure_model']=(exposures['hybrid_provenance']['interpretation'] if failure_mode=='hybrid' else 'Route/pylon engineering hourly probabilities; assumed hardening hazard multiplier 0.25')
    plan['replay']['weather_source']='imported' if imported else 'synthetic' if scenario=='synthetic' else 'historical_replay'
    plan['replay']['request']=descriptor  # /api/storm-plan rebuilds exposures from source, not client arrays
    plan['replay']['endpoint']='/api/storm-plan'
    # Preserve historical weather/observations as context; observations are never
    # changed by hypothetical interventions or used in portfolio selection.
    result={k:v for k,v in context.items() if k not in ('planning_weather','frames')}
    result.update(config=descriptor,plan=plan,replay=plan['replay'])
    if baseline is not None:
        result['engineering_plan']=baseline
        result['hazard_comparison']={key:{'engineering':baseline['portfolios'][key]['metrics'],
            'hybrid':plan['portfolios'][key]['metrics']} for key in ('none','custom') if key in plan['portfolios']}
        result['hybrid_provenance']=exposures['hybrid_provenance']
    result['frames']=map_frames(plan['portfolios']['none']['playback'],context)
    result['power_cut_forecast']=forecast(net,engineering,scenario,imported)
    result['chain']=chain_summary(engineering,plan,result.get('hybrid_provenance'),result['power_cut_forecast'])
    return result


def forecast(net,exposure,scenario,imported):
    """The ML's own output; a run without usable route weather reports why instead of failing."""
    try:
        from .benchmark import load_storm
        storm=load_storm(scenario) if not imported and scenario in STORMS else None
        return power_cut_forecast(net,exposure,storm,archive_storm=None if imported else scenario)
    except (ValueError,OSError,ImportError) as exc:
        return {'error':str(exc)}


def chain_summary(engineering,plan,provenance,cuts):
    """What each stage of the chain did, in order (the full detail stays in plan/engineering_plan)."""
    hours=engineering['hours']
    gust=lambda r:float(np.nanmax(np.asarray(r['gust_kmh'],float)))
    p=plan['portfolios']; none=p['none']['metrics']; rec=plan['recommendation']
    stages=[{'stage':'weather','source':engineering['source'],'hours':len(hours),'peak_gust_kmh':round(max(map(gust,hours)),1)},
            {'stage':'engineering_hazard','method':'Route/pylon fragility per hour (lognormal in local gust)',
             # Imported exposures without probabilities use the engine's own gust/rain hazard.
             'expected_line_failures':round(float(sum(sum(r['failure_probability']) for r in hours)),4) if 'failure_probability' in hours[0] else None}]
    if 'error' in cuts:
        stages.insert(1,{'stage':'ml_power_cut_forecast','applied':False,'reason':cuts['error']})
    else:
        c=cuts.get('comparison')
        stages.insert(1,{'stage':'ml_power_cut_forecast','applied':True,'model_version':cuts['model_version'],
            'feature_coverage':cuts['feature_coverage'],'lines_scored':cuts['lines_scored'],'hours_scored':cuts['hours_scored'],
            'expected_corridor_cuts':round(sum(cuts['expected_per_hour']),2),
            **({'observed_corridor_cuts':c['observed_total'],'expected_in_coverage':c['expected_total'],'lines_compared':c['lines_compared'],
                'timing_correlation':c['timing_correlation'],'peak_hour_expected':c['peak_hour_expected'],'peak_hour_observed':c['peak_hour_observed']} if c else {}),
            'note':'Local power-cut forecast near overhead routes; separate from transmission line failures'})
    if provenance:
        diag=[r['hybrid_diagnostics'] for r in plan['weather']['hours']]
        m=np.array([d['hazard_multiplier'] for d in diag])
        prediction=provenance['prediction']
        stages.append({'stage':'ml_hybrid','applied':True,'model_version':prediction['model_version'],
            'feature_coverage':prediction.get('feature_coverage',{}).get('mode','uploaded pair'),
            'lines_in_training_domain':int(np.sum(prediction.get('in_training_domain',[]))),
            'lines_tilted':int((np.abs(m-1)>1e-10).any(axis=0).sum()),'multiplier_range':[round(float(m.min()),3),round(float(m.max()),3)],
            'expected_line_failures':round(float(sum(sum(r['failure_probability']) for r in plan['weather']['hours'])),4),
            'note':'Hourly expected failures preserved; the ML moves failure probability between lines, which changes which lines break'})
    stages+=[{'stage':'failures_and_cascade','scenarios':plan['config']['samples'],'mean_failed_lines':none['mean_failed_lines'],
              'mean_cascade_trips':none['mean_cascade_trips'],'mean_unserved_mwh':none['mean_unserved_mwh'],
              'mean_nodes_ever_out_of_supply':none['mean_nodes_ever_out_of_supply'],
              'method':'DC flow per circuit with preventive/corrective redispatch, overload trips, islanding and supply loss'},
             {'stage':'mitigation','portfolios':{k:{'selected':v['selected'],'paired_gain_mwh':v['paired_gain_mwh'],
                                                    'net_benefit_gbp':v['economics']['net_benefit_gbp']} for k,v in p.items()},
              'method':'Every plan on the same held-out weather, failure and repair draws'},
             {'stage':'restoration','crews':plan['config']['crews'],'recovery_hours':plan['config'].get('recovery_hours',0),
              'method':'Calm post-storm steps: weather-gated crews repair damage (median 4 days) in priority order; no new weather damage',
              **{k:{f:p[k]['metrics'][f] for f in ('mean_repairs','mean_shed_mw','mean_outage_hours','recovered_fraction')}
                 for k in dict.fromkeys(['none',rec['portfolio']])}},
             {'stage':'recommendation',**rec}]
    return stages


@lru_cache(maxsize=4)
def storm_context(net,scenario,peak_gust,hours,seed,storm_type,median_gust=265.,dispersion=.12):
    if scenario=='synthetic':
        result=run_storm(net,peak_gust,hours,seed,storm_type,median_gust,dispersion)
    else:
        if scenario not in STORMS: raise ValueError('Unknown historical storm')
        result=run_real_storm(net,scenario,seed,median_gust,dispersion)
    return {**result,'network':net.payload()}
