"""Rebuild auditable circuit inventories and candidate incident tables; never train a model."""
from pathlib import Path
import json,re,hashlib,zipfile,xml.etree.ElementTree as ET
import pandas as pd
import numpy as np
import shapely
from pyproj import Transformer
ROOT=Path(__file__).resolve().parents[1]; RAW=ROOT/'data/raw'; OUT=ROOT/'data/processed'
OUT.mkdir(exist_ok=True)
MAN=json.loads((ROOT/'data/manifest.json').read_text())
COUNTS={}
def save(df,name):
 df.to_parquet(OUT/(name+'.parquet'),index=False);COUNTS[name]=len(df)
def read(p):
 try:return pd.read_csv(p,dtype=str,keep_default_na=False,encoding='utf-8-sig')
 except UnicodeDecodeError:return pd.read_csv(p,dtype=str,keep_default_na=False,encoding='cp1252')
def numeric(s):return pd.to_numeric(s,errors='coerce')
def nrn_parts(value):
 """Separator/padding normalization; candidate crosswalk, not operator-certified."""
 s=str(value).strip()
 if re.fullmatch(r'\d+\.\d+\.\d+\.\d+',s):
  a,b,*_=s.split('.');return f'{int(a)}:{int(b)}'
 if re.fullmatch(r'\d+_\d+',s):
  a,b=s.split('_');return f'{int(a)}:{int(b)}'
 if re.fullmatch(r'\d{7}',s):return f'{int(s[:4])}:{int(s[4:])}'
 return None

def inventories():
 p=next((RAW/'ssen_overhead_lines').glob('*.csv'));d=read(p)
 d=d.rename(columns={'id':'segment_id','route':'geometry_bng_wkt','route_lat_long':'geometry_wgs84_wkt'})
 d['voltage_kv']=numeric(d.nominal_voltage_pp.str.extract(r'([\d.]+)',expand=False));d['circuit_id']=d.circuit_id.str.replace(r'\.0$','',regex=True)
 g=shapely.from_wkt(d.geometry_bng_wkt.to_numpy(),on_invalid='ignore');d['length_km']=shapely.length(g)/1000
 c=shapely.centroid(g);conv=Transformer.from_crs(27700,4326,always_xy=True);d['longitude'],d['latitude']=conv.transform(shapely.get_x(c),shapely.get_y(c))
 d['circuit_key']='SSEN:GIS:'+d.circuit_id;d.loc[d.circuit_id.eq(''),'circuit_key']=pd.NA
 d['source_resource']=p.stem;save(d,'overhead_segments')
 rows=[]
 for key,x in d.dropna(subset=['circuit_key']).groupby('circuit_key'):
  nrns=sorted(set(x.circuit_nrn)-{''});volts=x.voltage_kv.dropna().unique();lat=x.latitude.mean();lon=x.longitude.mean()
  rows.append(dict(circuit_key=key,circuit_id=x.circuit_id.iloc[0],circuit_nrn=nrns[0] if len(nrns)==1 else None,nrn_values=json.dumps(nrns),voltage_kv=volts[0] if len(volts)==1 else None,overhead_length_km=x.length_km.sum(),segment_count=len(x),latitude=lat,longitude=lon,roster_scope='overhead_only',geometry_snapshot='2026-10',nrn_conflict=len(nrns)!=1))
 circuits=pd.DataFrame(rows);circuits['nrn_candidate_key']=circuits.circuit_nrn.map(nrn_parts)
 # Latitude is ONLY a licence-area heuristic, never an identifier proof.
 circuits['operator_candidate']=np.where(circuits.latitude>54,'SHEPD','SEPD')
 for col in ['underground_length_km','overhead_share','woodland_exposure_fraction','commissioning_year','customer_count','elevation_m','flood_depth_m','lightning_strikes','r_ohm','x_ohm','thermal_rating_mva','load_mw','generation_mw']:
  circuits[col]=np.nan
 circuits['electrical_model_join_status']='missing_crosswalk';circuits['training_ready']=False
 save(circuits,'circuits')
 sub=read(next((RAW/'ssen-substation-data').glob('*.csv')))
 sub['longitude'],sub['latitude']=conv.transform(numeric(sub.location_x_m),numeric(sub.location_y_m))
 sub['source_row']=np.arange(1,len(sub)+1);save(sub,'substations_all_statuses');save(sub[sub.status.str.lower().eq('existing')],'substations_existing')
 for folder in ['primary-supply-areas','secondary-supply-areas','bsp-gsp-supply-areas']:
  for p in (RAW/folder).glob('*.csv'):
   s=read(p);save(s,f'{folder}_{p.stem}')
 return circuits

def incidents(circuits):
 frames=[]
 for p in (RAW/'nafirs-hv-faults').glob('*.csv'):
  d=read(p);op='SHEPD' if 'NRN_NORTH' in d else 'SEPD';d['operator']=op
  d['network_reference']=d['NRN_NORTH' if op=='SHEPD' else 'NRN_SOUTH'];d['source_resource']=p.stem
  frames.append(d)
 d=pd.concat(frames,ignore_index=True).drop_duplicates()
 d.columns=d.columns.str.lower();d['event_key']=d.operator+':'+d.reporting_year+':'+d.dist_hv_ref
 d['event_key_duplicate']=d.event_key.duplicated(keep=False)
 d['reported_at_local']=pd.to_datetime(d.hv_incident_time,dayfirst=True,errors='coerce')
 # Source omits time zone. Retain local values; UTC conversion is explicitly provisional.
 d['reported_at_utc_assuming_london']=d.reported_at_local.dt.tz_localize('Europe/London',ambiguous='NaT',nonexistent='NaT').dt.tz_convert('UTC')
 d['timezone_status']='assumed_Europe_London_not_confirmed';d['timestamp_resolution_seconds']=60
 d['restored_at_utc']=pd.NaT
 for col in ['voltage_1','hv_cust_aff','hv_cust_mins_lost','avg_time_off_mins','damage']:d[col]=numeric(d[col])
 d['nrn_candidate_key']=d.network_reference.map(nrn_parts)
 lookup=circuits.dropna(subset=['nrn_candidate_key']).copy()
 lookup['join_key']=lookup.operator_candidate+':'+lookup.nrn_candidate_key
 counts=lookup.join_key.value_counts();lookup=lookup[lookup.join_key.map(counts).eq(1)].set_index('join_key')
 d['circuit_key_candidate']=(d.operator+':'+d.nrn_candidate_key).map(lookup.circuit_key)
 d['join_status']=np.where(d.circuit_key_candidate.notna(),'candidate_NRN_format_crosswalk_requires_confirmation','unmatched')
 # No planned-status field exists. Never promote cause-based inference to confirmed unplanned.
 d['planned_status']='unknown_not_published';d['training_eligible']=False
 text=d.cause.fillna('')+' '+d.contributory_cause.fillna('')
 operational=text.str.contains(r'(?<!un)planned|Operational or Safety Restriction|remove local generator|restore temporary connections|Testing or [Cc]ommissioning',case=False,regex=True)
 d['exclusion_reason']=np.where(operational,'possible_planned_or_operational_interruption','planned_status_unverified')
 save(d[~operational],'fault_candidates_UNVERIFIED');save(d[operational][['event_key','exclusion_reason','source_resource']],'excluded_incident_audit')
 # Strict output is intentionally empty until explicit status/coverage are supplied.
 save(d.iloc[:0],'unplanned_incidents_VERIFIED')
 COUNTS.update(incident_start=str(d.reported_at_local.min()),incident_end=str(d.reported_at_local.max()),candidate_circuit_matches=int((~operational & d.circuit_key_candidate.notna()).sum()),candidate_match_fraction=float(d.loc[~operational,'circuit_key_candidate'].notna().mean()),planned_status_missing=int(len(d)))

def electrical():
 for r in MAN:
  if 'System Capacity Existing' not in r['name']:continue
  op='SHEPD' if r['name'].startswith('SHEPD') else 'SEPD';z=zipfile.ZipFile(ROOT/r['path']);allrows=[]
  for member in z.namelist():
   if not member.endswith('.xml'):continue
   root=ET.fromstring(z.read(member))
   for e in root:
    eid=next((v.lstrip('#') for k,v in e.attrib.items() if k.endswith('}ID') or k.endswith('}about')),None)
    for child in e:
     ref=next((v for k,v in child.attrib.items() if k.endswith('}resource')),None)
     allrows.append({'operator':op,'profile':member,'object_id':eid,'class':e.tag.split('}')[-1],'property':child.tag.split('}')[-1],'value':child.text.strip() if child.text else None,'reference':ref.lstrip('#') if ref else None,'source_resource':r['resource_id']})
  df=pd.DataFrame(allrows);save(df,f'cim_properties_{op}')
  eq=df[df.profile.str.endswith('_EQ.xml')].copy();eq['cell']=eq.reference.fillna(eq.value)
  for cls in ['Terminal','ConnectivityNode','ACLineSegment','PowerTransformerEnd','PowerTransformer','EnergyConsumer','GeneratingUnit','EquivalentInjection','Breaker','Disconnector','BaseVoltage','OperationalLimitSet','CurrentLimit','CurrentLimitDataPoint','ApparentPowerLimitDataPoint']:
   part=eq[eq['class'].eq(cls)].pivot_table(index='object_id',columns='property',values='cell',aggfunc='first').reset_index();save(part,f'cim_{op}_{cls}')
 # Normalise all workbook sheets with their multi-row headers retained separately.
 for r in MAN:
  if 'long_term_development_statement' not in r['dataset'] or not r['path'].endswith('.xlsx'):continue
  x=pd.ExcelFile(ROOT/r['path']);dest=OUT/'ltds_workbooks'/r['resource_id'];dest.mkdir(parents=True,exist_ok=True)
  for i,sheet in enumerate(x.sheet_names):
   df=pd.read_excel(x,sheet_name=sheet,header=None,dtype=str);df.columns=[f'column_{i}' for i in range(df.shape[1])];df.to_parquet(dest/f'{i}.parquet',index=False)
  (dest/'sheets.json').write_text(json.dumps(x.sheet_names))
  if 'Tables 1-7' not in r['name']:continue
  op='SHEPD' if r['dataset'].startswith('shepd') else 'SEPD';df=pd.read_excel(x,sheet_name=0,skiprows=3,header=None)
  cols=['gsp_group','from_bus','to_bus','voltage_kv']+(['circuit_number'] if op=='SHEPD' else [])+['r_percent_100mva','x_percent_100mva','b_percent_100mva','rating_winter_mva','rating_spring_autumn_mva','rating_summer_mva','length_km']
  df=df.iloc[:,:len(cols)];df.columns=cols
  for c in cols:
   if c not in ['gsp_group','from_bus','to_bus','circuit_number']:df[c]=numeric(df[c])
  df=df[df.voltage_kv.notna()].copy();
  for col in ['from_bus','to_bus','gsp_group','circuit_number']:
   if col in df:df[col]=df[col].astype('string')
  df['source_row']=df.index+4;df['operator']=op;df['source_resource']=r['resource_id'];df['snapshot']='2026-05'
  df['branch_key']=op+':LTDS202605:'+df.source_row.astype(str)
  for c in ['r','x']:df[c+'_ohm']=df[c+'_percent_100mva']/100*df.voltage_kv**2/100
  df['b_siemens']=df.b_percent_100mva/100*100/df.voltage_kv**2
  df['zero_impedance_requires_topology_processing']=df.r_ohm.eq(0)&df.x_ohm.eq(0)
  save(df,f'electrical_branches_{op}')

def supporting():
 for r in MAN:
  if r['dataset'] not in {'embedded_capacity_register','generation-availability-and-network-capacity'} or not r['path'].endswith('.csv'):continue
  try:
   d=read(ROOT/r['path']);d['source_resource']=r['resource_id'];save(d,'support_'+r['resource_id'])
  except (pd.errors.ParserError,UnicodeDecodeError) as e:COUNTS['parse_error_'+r['resource_id']]=str(e)
if __name__=='__main__':
 c=inventories();incidents(c);electrical();supporting();(OUT/'quality_report.json').write_text(json.dumps(COUNTS,indent=2));print(json.dumps(COUNTS,indent=2))
