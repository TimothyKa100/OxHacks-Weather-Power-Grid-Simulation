"""Type electrical quantities and resolve published limit-to-equipment references."""
from pathlib import Path
import pandas as pd
P=Path(__file__).resolve().parents[1]/'data/processed'
def main():
 for op in ['SEPD','SHEPD']:
  properties=pd.read_parquet(P/f'cim_properties_{op}.parquet');properties=properties[properties.profile.str.endswith('_EQ.xml')].copy();properties['cell']=properties.reference.fillna(properties.value)
  def table(cls):return properties[properties['class'].eq(cls)].pivot_table(index='object_id',columns='property',values='cell',aggfunc='first').reset_index()
  winding=table('PowerTransformerEnd').rename(columns={'object_id':'winding_id','PowerTransformerEnd.PowerTransformer':'transformer_id','TransformerEnd.Terminal':'terminal_id','IdentifiedObject.name':'name'})
  for source,target in [('PowerTransformerEnd.r','r_ohm'),('PowerTransformerEnd.x','x_ohm'),('PowerTransformerEnd.b','b_siemens'),('PowerTransformerEnd.g','g_siemens'),('PowerTransformerEnd.ratedS','rating_mva'),('PowerTransformerEnd.ratedU','voltage_kv'),('TransformerEnd.endNumber','winding_number')]:winding[target]=pd.to_numeric(winding[source],errors='coerce')
  winding['operator']=op;winding.to_parquet(P/f'transformer_windings_{op}.parquet',index=False)
  limits=[];sets=table('OperationalLimitSet').set_index('object_id')['OperationalLimitSet.Terminal'];term=table('Terminal').set_index('object_id')['Terminal.ConductingEquipment']
  for cls,unit in [('CurrentLimit','A'),('ApparentPowerLimit','MVA')]:
   base=table(cls);seriescol=f'{cls}.{cls}Series';base=base.rename(columns={'object_id':'limit_id',seriescol:'series_id'})
   point=table(cls+'DataPoint');point=point.rename(columns={'object_id':'data_point_id',f'{cls}DataPoint.{cls}Series':'series_id','IdentifiedObject.name':'season'})
   d=point.merge(base[['limit_id','series_id','OperationalLimit.OperationalLimitSet','OperationalLimit.OperationalLimitType']],on='series_id',how='left',validate='many_to_many')
   d['terminal_id']=d['OperationalLimit.OperationalLimitSet'].map(sets);d['equipment_id']=d.terminal_id.map(term);d['rating']=pd.to_numeric(d[f'{cls}DataPoint.normalValue'],errors='coerce');d['unit']=unit;d['operator']=op
   limits.append(d[['operator','data_point_id','limit_id','series_id','equipment_id','terminal_id','season','rating','unit','OperationalLimit.OperationalLimitType']])
  pd.concat(limits,ignore_index=True).to_parquet(P/f'equipment_seasonal_ratings_{op}.parquet',index=False)
  gen=table('GeneratingUnit');gen['max_net_power_mw']=pd.to_numeric(gen['GeneratingUnit.ratedNetMaxP'],errors='coerce');gen['operator']=op;gen.to_parquet(P/f'electrical_generators_{op}.parquet',index=False)
  print(op,'windings',len(winding),'ratings',sum(len(x) for x in limits),flush=True)
if __name__=='__main__':main()
