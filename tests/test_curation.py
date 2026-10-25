import unittest,sys
from pathlib import Path
import numpy as np,pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from weather import features
from curate import nrn_parts
class CurationTests(unittest.TestCase):
 def test_exposure_cannot_see_current_or_future_weather(self):
  d=pd.DataFrame({'time':pd.date_range('2021-01-01',periods=100,freq='h'),'wind_gusts_10m':90.,'temperature_2m':10.,'precipitation':0.})
  a=features(d);d.loc[50:,'wind_gusts_10m']=150.;b=features(d)
  cols=[c for c in a if '_prev_' in c]
  pd.testing.assert_frame_equal(a.loc[:50,cols],b.loc[:50,cols])
  self.assertEqual(b.loc[51,'gust_ge_100_kmh_sample_hours_prev_6h'],1)
 def test_missing_weather_is_not_safe_exposure(self):
  d=pd.DataFrame({'time':pd.date_range('2021-01-01',periods=30,freq='h'),'wind_gusts_10m':90.,'temperature_2m':10.,'precipitation':0.});d.loc[10,'wind_gusts_10m']=np.nan
  self.assertTrue(pd.isna(features(d).loc[11,'gust_ge_80_kmh_sample_hours_prev_6h']))
 def test_hourly_gaps_rejected(self):
  d=pd.DataFrame({'time':['2021-01-01T00:00','2021-01-01T02:00']})
  with self.assertRaises(ValueError):features(d)
 def test_leading_zeros_and_operator_specific_nrn_formats(self):
  self.assertEqual(nrn_parts('0545.301.000.00'),nrn_parts('545_301'))
  self.assertEqual(nrn_parts('4905004'),'4905:4')
  self.assertIsNone(nrn_parts('947-T1/T2'))
 def test_unknown_planning_status_never_enters_training(self):
  root=Path(__file__).resolve().parents[1]/'data/processed'
  d=pd.read_parquet(root/'unplanned_incidents_VERIFIED.parquet');self.assertEqual(len(d),0)
  q=pd.read_parquet(root/'fault_candidates_UNVERIFIED.parquet');self.assertFalse(q.training_eligible.any())
 def test_electrical_connectivity_has_no_dangling_nodes(self):
  root=Path(__file__).resolve().parents[1]/'data/processed'
  for op in ['SEPD','SHEPD']:
   t=pd.read_parquet(root/f'electrical_terminals_{op}.parquet');self.assertTrue(t.node_reference_resolved.all())
 def test_electrical_unit_conversion(self):
  root=Path(__file__).resolve().parents[1]/'data/processed'
  d=pd.read_parquet(root/'electrical_branches_SEPD.parquet');row=d[(d.voltage_kv==132)&(d.r_percent_100mva>0)].iloc[0]
  self.assertAlmostEqual(row.r_ohm,row.r_percent_100mva*1.7424)
if __name__=='__main__':unittest.main()
