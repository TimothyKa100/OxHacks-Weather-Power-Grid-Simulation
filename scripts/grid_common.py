"""Shared graph-data schema; physical line equipment is represented by a node."""
from pathlib import Path
import json,math,re
import pandas as pd,numpy as np
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'data/grid';OUT=BASE/'normalized';RAW=BASE/'raw';OUT.mkdir(parents=True,exist_ok=True)
def clean(v):
 if isinstance(v,dict):return {str(k):clean(x) for k,x in v.items()}
 if isinstance(v,(list,tuple,np.ndarray)):return [clean(x) for x in v]
 if isinstance(v,np.generic):v=v.item()
 if v is None or v is pd.NA or isinstance(v,float) and not math.isfinite(v):return None
 return v

def js(v):return json.dumps(clean(v),default=str,ensure_ascii=False,allow_nan=False)
def num(v):
 try:return float(v) if math.isfinite(float(v)) else None
 except (TypeError,ValueError):return None

def node(id,type,source,**kw):
 row=dict(node_id=str(id),node_type=type,source=source,source_asset_id=str(id),operator=None,name=None,latitude=None,longitude=None,geometry_wgs84_wkb=None,location_method=None,voltage_kv=None,r_ohm=None,x_ohm=None,b_siemens=None,capacity_mva=None,generation_capacity_mw=None,length_km=None,lifecycle_status='existing_as_published',topology_layer=source,electrical_parameters_status='not_published',properties_json='{}',source_ref=None)
 row.update(kw);return row

def edge(a,b,source,kind='electrical_connection',evidence='published_model',**kw):
 if a is None or b is None or a is pd.NA or b is pd.NA or str(a) in ['None','nan',''] or str(b) in ['None','nan','']:raise ValueError('A graph relationship needs two actual source-scoped identifiers')
 return dict(from_node=str(a),to_node=str(b),source=source,relation=kind,evidence=evidence,**kw)
def save_nodes(rows,name):
 d=pd.DataFrame(rows)
 for col in ['latitude','longitude','voltage_kv','r_ohm','x_ohm','b_siemens','capacity_mva','generation_capacity_mw','length_km']:
  if col in d:d[col]=pd.to_numeric(d[col],errors='coerce')
 d.to_parquet(OUT/(name+'_nodes.parquet'),index=False);return d
def save_edges(rows,name):
 d=pd.DataFrame(rows,columns=['from_node','to_node','source','relation','evidence']);d.drop_duplicates().to_parquet(OUT/(name+'_edges.parquet'),index=False);return d

def stream_nodes(iterator,name,chunk_size=25000):
 import pyarrow as pa,pyarrow.parquet as pq
 sample=node('','', '');numeric={'latitude','longitude','voltage_kv','r_ohm','x_ohm','b_siemens','capacity_mva','generation_capacity_mw','length_km'}
 schema=pa.schema([(key,pa.binary() if key=='geometry_wgs84_wkb' else pa.float64() if key in numeric else pa.string()) for key in sample]);path=OUT/(name+'_nodes.parquet');count=0;batch=[]
 with pq.ParquetWriter(path,schema,compression='zstd') as writer:
  for row in iterator:
   batch.append(row)
   if len(batch)>=chunk_size:writer.write_table(pa.Table.from_pylist(batch,schema=schema));count+=len(batch);batch=[]
  if batch:writer.write_table(pa.Table.from_pylist(batch,schema=schema));count+=len(batch)
 return count
