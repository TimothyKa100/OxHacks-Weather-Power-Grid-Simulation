"""Preserve NESO ETYS transmission branches and parameters without guessing bus voltage bases."""
import re,json
import pandas as pd
from grid_common import RAW,OUT,BASE,node,edge,save_nodes,save_edges,js,num

def main():
 x=pd.ExcelFile(RAW/'neso_etys2025_appendix_b.xlsx');nodes={};edges=[];tables=[]
 for sheet in x.sheet_names:
  if not re.match(r'B-[23]-1[a-z]$',sheet):continue
  raw=pd.read_excel(x,sheet_name=sheet,header=None,dtype=str);header=None
  for i,row in raw.head(12).iterrows():
   names=[re.sub(r'\s+','',str(v)).casefold() for v in row]
   if 'node1' in names and 'node2' in names:header=i;break
  if header is None:continue
  cols=[str(v).strip() if pd.notna(v) else 'column_'+str(i) for i,v in enumerate(raw.iloc[header])];d=raw.iloc[header+1:].copy();d.columns=cols
  a=next(c for c in cols if re.sub(r'\s+','',c).casefold()=='node1');b=next(c for c in cols if re.sub(r'\s+','',c).casefold()=='node2');d=d[d[a].notna() & d[b].notna()];d.to_parquet(OUT/f'NESO_{sheet}_table.parquet',index=False)
  for idx,row in d.iterrows():
   props=row.to_dict();left,right=str(row[a]).strip(),str(row[b]).strip()
   if not left or not right:continue
   for code in [left,right]:
    id='NESO:ETYS2025:bus:'+code
    nodes.setdefault(id,node(id,'junction','NESO_ETYS2025',source_asset_id=code,name=code,location_method=None,properties_json=js({'node_code':code,'nominal_voltage_status':'not_decoded_from_code'}),source_ref=f'neso_etys2025_appendix_b.xlsx:{sheet}'))
   id=f'NESO:ETYS2025:{sheet}:row:{idx+1}';type='power_line' if sheet.startswith('B-2') else 'transformer';kwargs={}
   lengths=[num(v) for k,v in props.items() if 'length' in k.casefold() and 'km' in k.casefold()];lengths=[v for v in lengths if v is not None]
   ratings=[num(v) for k,v in props.items() if k.casefold()=='rating (mva)'];ratings=[v for v in ratings if v is not None]
   if lengths:kwargs['length_km']=sum(lengths)
   if ratings:kwargs['capacity_mva']=ratings[0]
   nodes[id]=node(id,type,'NESO_ETYS2025',source_asset_id=f'{sheet}:{idx+1}',name=f'{left} - {right}',electrical_parameters_status='published_ETYS_percent_on_100MVA_base_preserved',properties_json=js(props),source_ref=f'neso_etys2025_appendix_b.xlsx:{sheet}:row:{idx+1}',**kwargs)
   for code in [left,right]:edges.append(edge(id,'NESO:ETYS2025:bus:'+code,'NESO_ETYS2025','electrical_connection','published_ETYS_endpoint_codes'))
  tables.append({'sheet':sheet,'rows':len(d)})
 save_nodes(list(nodes.values()),'NESO_transmission');save_edges(edges,'NESO_transmission');(BASE/'reports/etys_report.json').write_text(json.dumps(tables,indent=2));print('NESO',len(nodes),'nodes',len(edges),'incidences',flush=True)
if __name__=='__main__':main()
