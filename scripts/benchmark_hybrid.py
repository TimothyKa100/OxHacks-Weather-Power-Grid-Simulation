"""Reproducible GB integration sensitivity experiment using SYNTHETIC paired scores.

This demonstrates coupling and uncertainty, not accuracy against observed line failures.
"""
import argparse,json,sys
from pathlib import Path
import numpy as np
from scipy.special import expit,logit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.simulation import Network,simulate
from backend.storm import run_storm
from backend.weather import multiday_scenarios
from backend.hybrid import hybrid_weather
from backend.decisions import paired_evidence


def pair_fixture(exposure):
    ids=exposure['branch_ids']; rows=[]
    # Deliberately arbitrary residual pattern, not fitted or inferred asset condition.
    residual=1.5*np.sin(np.arange(len(ids))*2*np.pi/37)
    for r in exposure['hours']:
        reference=.001+.02*expit((np.asarray(r['gust_kmh'])-100)/20)
        full=expit(logit(reference)+residual)
        rows.append(dict(timestamp=r['timestamp'],full_probability=full.tolist(),reference_probability=reference.tolist()))
    return dict(source='SYNTHETIC SCORE FIXTURE: analytic reference plus arbitrary branch-index residual; not trained ML',
        prediction_unit='paired_risk_modifier',branch_ids=ids,hours=rows,
        prediction=dict(model_version='synthetic-residual-fixture',reference_model_version='synthetic-wind-reference',
            target='transmission_weather_failure',evidence_status='synthetic_fixture',
            training_cutoff='2025-01-01T00:00:00Z',issued_at=rows[0]['timestamp']))


def main(destination,samples=16):
    net=Network('gb'); rows=[]; reference_geometry=None
    for median in (250,265,300):
        exposure=run_storm(net,190,12,7,median_gust=median)['planning_weather']
        gusts=[r['gust_kmh'] for r in exposure['hours']]
        if reference_geometry is None: reference_geometry=gusts
        assert gusts==reference_geometry,'Fragility sweep must keep the weather fixed'
        pair=pair_fixture(exposure)
        for seed in (7,26,42):
            base_bank=multiday_scenarios(net,190,seed,samples,weather=exposure,crews=6)
            baseline=[simulate(net,s) for s in base_bank]
            before=np.array([r['unserved_mwh'] for r in baseline])
            for beta in (0,.5,1):
                bank=multiday_scenarios(net,190,seed,samples,weather=hybrid_weather(exposure,pair,{'beta':beta}),crews=6)
                assert np.array_equal(base_bank[0]['draws'],bank[0]['draws'])
                assert np.array_equal(base_bank[0]['repair_hours'],bank[0]['repair_hours'])
                results=[simulate(net,s) for s in bank]
                after=np.array([r['unserved_mwh'] for r in results])
                if beta==0: assert np.array_equal(before,after)
                rows.append(dict(median_gust=median,beta=beta,failure_seed=seed,samples=samples,
                    engineering_mean_mwh=float(before.mean()),hybrid_mean_mwh=float(after.mean()),
                    mean_distinct_failed_lines=float(np.mean([r['failed_lines'] for r in results])),
                    engineering_mean_distinct_failed_lines=float(np.mean([r['failed_lines'] for r in baseline])),
                    mean_additional_loss_mwh=float((after-before).mean()),paired_evidence=paired_evidence(before,after)))
    report=dict(evidence_status='synthetic_fixture',network='gb',buses=net.n,branches=net.m,storm_seed=7,hours=12,peak_gust_kmh=190,
        purpose='Integration/sensitivity only. No new empirical failure model or reference has been fitted to real data.',
        assumptions='Wind and draws fixed across median/beta comparisons. Arbitrary synthetic residual pattern. No reinforcement in this sweep.',
        multiplier_bounds=[.5,2.],rows=rows)
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps(dict(output=str(destination),cases=len(rows),
        maximum_mean_additional_loss_mwh=max(r['mean_additional_loss_mwh'] for r in rows),
        minimum_mean_additional_loss_mwh=min(r['mean_additional_loss_mwh'] for r in rows)),indent=2))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',default='docs/results/hybrid_sensitivity.json');p.add_argument('--samples',type=int,default=16)
    args=p.parse_args();main(args.output,args.samples)
