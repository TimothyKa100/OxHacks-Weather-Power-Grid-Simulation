"""Portable weather-aware logistic reference and separate margin calibration."""
import numpy as np
from scipy.special import expit

REFERENCE_FEATURES = ['gust_max_kmh','gust_p95_kmh','gust_prev_3h_max_kmh','crosswind_max_kmh',
                      'length_km','ele_mean_m','ele_max_m','kv','circuits']


def margin(frame, artifact):
    x=np.asarray(frame[artifact['features']],float)
    x=np.where(np.isnan(x),artifact['medians'],x)
    return ((x-np.array(artifact['means']))/np.array(artifact['scales']))@np.array(artifact['coefficients'])+artifact['intercept']


def predict(frame, artifact):
    return expit(artifact['calibration']['coefficient']*margin(frame,artifact)+artifact['calibration']['intercept'])


def calibrator(margins, labels):
    from sklearn.linear_model import LogisticRegression
    if len(np.unique(labels))!=2: raise ValueError('Calibration requires positive and negative labels')
    model=LogisticRegression(C=1e6,max_iter=1000).fit(np.asarray(margins).reshape(-1,1),labels)
    if model.coef_[0,0]<=0: raise ValueError('Calibration reverses risk ranking; investigate model/data before export')
    return {'coefficient':float(model.coef_[0,0]),'intercept':float(model.intercept_[0])}


def fit(fitting, calibration, features=REFERENCE_FEATURES):
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    imputer=SimpleImputer(strategy='median',keep_empty_features=True)
    x=imputer.fit_transform(fitting[features])
    scaler=StandardScaler().fit(x)
    model=LogisticRegression(max_iter=1000).fit(scaler.transform(x),fitting.failed_next_hour)
    artifact={'features':features,'medians':imputer.statistics_.tolist(),'means':scaler.mean_.tolist(),
              'scales':scaler.scale_.tolist(),'coefficients':model.coef_[0].tolist(),'intercept':float(model.intercept_[0])}
    artifact['calibration']=calibrator(margin(calibration,artifact),calibration.failed_next_hour)
    return artifact
