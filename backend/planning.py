"""GB map adapter for the multi-day simulation and mitigation decision engine."""
from functools import lru_cache
from .benchmark import STORMS, run_real_storm
from .storm import run_storm
from .simulation import run_experiment
from .hybrid import hybrid_weather, settings, fragility_settings


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
                   custom_lines=None, economics=None, weather=None, failure_mode="engineering", hybrid_profile=None, hybrid=None, fragility=None):
    fragility=fragility_settings(fragility)
    imported=weather is not None
    context=({'network':net.payload(),'frames':[],'input_source':weather['source']} if imported
             else storm_context(net,scenario,peak_gust,hours,seed,storm_type,**fragility))
    exposures=weather if imported else context['planning_weather']
    baseline=None
    config=settings(hybrid)
    if failure_mode=='hybrid':
        baseline=run_experiment(net,seed=seed,samples=samples,budget=budget,mode='multiday',
            hours=len(exposures['hours']),crews=crews,weather=exposures,storm_type=storm_type,custom_lines=custom_lines,economics=economics)
        exposures=hybrid_weather(exposures,hybrid_profile,config)
    plan=run_experiment(net,seed=seed,samples=samples,budget=budget,mode='multiday',
                        hours=len(exposures['hours']),crews=crews,weather=exposures,
                        storm_type=storm_type,custom_lines=custom_lines,economics=economics)
    descriptor=dict(scenario=scenario,peak_gust=peak_gust,hours=len(exposures['hours']),seed=seed,
                    storm_type=storm_type,samples=samples,budget=budget,crews=crews,
                    custom_lines=custom_lines,economics=plan['config']['economics'],weather=weather,
                    failure_mode=failure_mode,hybrid_profile=hybrid_profile,hybrid=config,fragility=fragility)
    plan['model']['weather']=exposures['source']
    if 'engineering_model' in exposures:
        plan['model']['failure_model']=('Experimental hybrid hazard: engineering × bounded paired-ML residual; not calibrated transmission reliability' if failure_mode=='hybrid' else 'Route/pylon engineering hourly probabilities; assumed hardening hazard multiplier 0.25')
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
    return result


@lru_cache(maxsize=4)
def storm_context(net,scenario,peak_gust,hours,seed,storm_type,median_gust=265.,dispersion=.12):
    if scenario=='synthetic':
        result=run_storm(net,peak_gust,hours,seed,storm_type,median_gust,dispersion)
    else:
        if scenario not in STORMS: raise ValueError('Unknown historical storm')
        result=run_real_storm(net,scenario,seed,median_gust,dispersion)
    return {**result,'network':net.payload()}
