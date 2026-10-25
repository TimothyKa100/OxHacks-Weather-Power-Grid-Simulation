import copy,json
import numpy as np
import pandas as pd
import pytest
from failure_model.paired import train,export,validate_rows
from failure_model.gb_lines import FEATURES


def rows_fixture():
    rng=np.random.default_rng(26); rows=[]
    for group,name in enumerate(['fit','tune','calibrate','test']):
        for hour in range(40):
            for asset in range(20):
                gust=float(rng.uniform(30,160));rain=float(rng.uniform(0,20))
                p=1/(1+np.exp(-((gust-100)/15+rain/15-1)))
                r={f:float(rng.normal()) for f in FEATURES}
                r.update(gust_max_kmh=gust,gust_p95_kmh=gust*.9,gust_prev_3h_max_kmh=gust*.8,crosswind_max_kmh=gust*.7,
                    rain_1h_mm=rain,length_km=10+asset,kv=132,ele_mean_m=50,ele_max_m=100,circuits=1,
                    asset_id=f'L{asset+1:02d}',timestamp_utc=pd.Timestamp('2024-01-01',tz='UTC')+pd.Timedelta(hours=group*100+hour),
                    storm_id=f'fixture-{group}',split=name,failed_next_hour=int(rng.random()<p),
                    operational_at_start=True,reporting_complete=True)
                rows.append(r)
    return pd.DataFrame(rows)


CONTRACT=dict(target='transmission_weather_failure',evidence_status='synthetic_fixture',source='Artificial end-to-end test, not observed failures',features=FEATURES)


def test_train_save_export_pair(tmp_path):
    rows=rows_fixture(); model=tmp_path/'fixture-pair'
    result=train(rows,CONTRACT,model)
    assert result['evidence_status']=='synthetic_fixture'
    assert (model/'reference_model.json').is_file()
    infer=rows[rows.split.eq('test')].copy()
    network=tmp_path/'network.json';network.write_text(json.dumps({'branches':[{'id':f'L{i:02d}'} for i in range(1,21)]}))
    pair=export(infer,model,tmp_path/'pair.json',network)
    assert len(pair['hours'])==40 and len(pair['hours'][0]['full_probability'])==20
    assert pair['prediction']['target']=='transmission_weather_failure'
    with pytest.raises(ValueError):train(rows,CONTRACT,model)
    (model/'reference_model.json').write_text('{}')
    with pytest.raises(ValueError,match='checksum'):export(infer,model,tmp_path/'bad.json',network)


def test_training_rejects_leakage_and_unknown_exposure():
    rows=rows_fixture();validate_rows(rows,CONTRACT)
    bad=rows.copy();bad.loc[bad.split.eq('test'),'storm_id']='fixture-0'
    with pytest.raises(ValueError,match='Storm'):validate_rows(bad,CONTRACT)
    bad=rows.copy();bad.loc[0,'operational_at_start']=False
    with pytest.raises(ValueError,match='operational'):validate_rows(bad,CONTRACT)
    with pytest.raises(ValueError):validate_rows(rows,{**CONTRACT,'features':FEATURES+['outage_duration']})


def test_legacy_calibration_line_endings_are_verified():
    from failure_model.gb_model import load_model
    model,calibration,manifest=load_model()
    assert calibration==manifest['calibration']
    assert model is not None
