"""Bring located operator inventories and official generation records into the common asset schema."""
import json,re
import numpy as np,pandas as pd,shapely
from shapely.geometry import Point
from pyproj import Transformer
from grid_common import ROOT,BASE,OUT,RAW,node,edge,save_nodes,save_edges,js,num
CONV=Transformer.from_crs(27700,4326,always_xy=True)
def point(x,y):
 x,y=num(x),num(y)
 if x is None or y is None:return None,None,None
 lon,lat=CONV.transform(x,y)
 if not(-10<lon<3 and 49<lat<62):return None,None,None
 return lon,lat,Point(lon,lat).wkb

def distinguish_site_records(rows):
 groups={}
 for row in rows:groups.setdefault(row['node_id'],[]).append(row)
 result=[];relations=[]
 for id,group in groups.items():
  if len(group)==1:result.extend(group);continue
  parent=dict(group[0]);parent.update(name='Site '+parent['source_asset_id'],voltage_kv=None,capacity_mva=None,properties_json=js({'source_substation_id':parent['source_asset_id'],'inventory_records':len(group),'note':'Multiple equipment/voltage records share a published site identifier; see contained source records.'}))
  if len({(r['latitude'],r['longitude']) for r in group})>1:parent.update(latitude=None,longitude=None,geometry_wgs84_wkb=None)
  result.append(parent)
  for number,row in enumerate(group,1):
   child=dict(row,node_id=id+':inventory_record:'+str(number),node_type='substation_equipment_record');result.append(child);relations.append(edge(id,child['node_id'],'NPG_inventory','contains','shared_published_site_identifier'))
 return result,relations

def operator_inventories():
 # Every original source record is retained; unverified identities are not merged across datasets.
 d=pd.read_parquet(ROOT/'data/processed/substations_existing.parquet');rows=[]
 for i,r in enumerate(d.to_dict('records')):
  uid=f'SSEN:inventory:{r["source_row"]}';lon,lat=num(r.get('longitude')),num(r.get('latitude'))
  rows.append(node(uid,'substation','SSEN_inventory',source_asset_id=str(r.get('number')),operator=r.get('owner_name'),name=r.get('locality'),longitude=lon,latitude=lat,geometry_wgs84_wkb=Point(lon,lat).wkb if lon is not None and lat is not None else None,location_method='published_site_coordinates',properties_json=js(r),source_ref='data/processed/substations_existing.parquet'))
 save_nodes(rows,'SSEN_inventory')
 path=RAW/'npg/substation_sites_list.json';rows=[];hierarchy=[]
 d=json.loads(path.read_text())
 lookup={}
 for r in d:lookup.setdefault((r.get('dno_area'),str(r.get('site_name')).casefold()),[]).append(str(r['substation_id']))
 for r in d:
  id='NPG:substation:'+str(r['substation_id']);g=r.get('latlong') or {};lon,lat=num(g.get('lon')),num(g.get('lat'));s=num(r.get('transformer_rating_kva'));s=s/1000 if s is not None else num(r.get('firm_capacity_mva'))
  rows.append(node(id,'substation','NPG_inventory',source_asset_id=r['substation_id'],operator='Northern Powergrid:'+str(r.get('dno_area')),name=r.get('site_name'),longitude=lon,latitude=lat,geometry_wgs84_wkb=Point(lon,lat).wkb if lon is not None and lat is not None else None,location_method='published_site_coordinates',voltage_kv=num(r.get('primary_voltage_kv')),capacity_mva=s,properties_json=js(r),source_ref=str(path.relative_to(ROOT))))
  matches=lookup.get((r.get('dno_area'),str(r.get('upstream_substation')).casefold()),[])
  if len(matches)==1:hierarchy.append(edge('NPG:substation:'+matches[0],id,'NPG_inventory','supplies','published_upstream_name_unique_within_DNO'))
 rows,contained=distinguish_site_records(rows);hierarchy.extend(contained);save_nodes(rows,'NPG_inventory');save_edges(hierarchy,'NPG_supply_hierarchy')
 # SSEN feeder and GIS circuit aggregates remain distinct from their physical route segments.
 rows=[];relations=[];c=pd.read_parquet(ROOT/'data/processed/circuit_roster.parquet')
 for r in c.to_dict('records'):
  props={k:v for k,v in r.items() if k!='geometry_wgs84_wkb'};rows.append(node(r['circuit_key'],'feeder','SSEN_feeder_inventory',source_asset_id=r['crosswalk_key'],operator=r['operator'],name=r['primary_name']+':'+r['hv_feeder_nrn'],longitude=r['longitude'],latitude=r['latitude'],geometry_wgs84_wkb=r['geometry_wgs84_wkb'],location_method='published_supply_area_representative_point',voltage_kv=r.get('voltage_kv'),properties_json=js(props),source_ref='data/processed/circuit_roster.parquet'))
 save_nodes(rows,'SSEN_feeders')
 c=pd.read_parquet(ROOT/'data/processed/circuits.parquet');rows=[]
 for r in c.to_dict('records'):
  rows.append(node(r['circuit_key'],'circuit','SSEN_GIS_circuits',source_asset_id=r['circuit_id'],longitude=r['longitude'],latitude=r['latitude'],location_method='route_segment_centroid_average',voltage_kv=r.get('voltage_kv'),length_km=r['overhead_length_km'],properties_json=js(r),source_ref='data/processed/circuits.parquet'))
 save_nodes(rows,'SSEN_circuits')
 d=pd.read_parquet(ROOT/'data/processed/overhead_segments.parquet');wood=pd.read_parquet(ROOT/'data/processed/segment_woodland_exposure.parquet').set_index('segment_id');rows=[];junctions={};edges=[]
 for r in d.to_dict('records'):
  id='SSEN:line:'+r['segment_id'];geom=shapely.from_wkt(r['geometry_wgs84_wkt']);props={k:v for k,v in r.items() if not k.startswith('geometry_')}
  if r['segment_id'] in wood.index:props['woodland_within_30m_km']=num(wood.loc[r['segment_id'],'nfi_2024_within_30m_km'])
  rows.append(node(id,'power_line','SSEN_GIS',source_asset_id=r['segment_id'],longitude=r['longitude'],latitude=r['latitude'],geometry_wgs84_wkb=geom.wkb,location_method='published_route',voltage_kv=r['voltage_kv'],length_km=r['length_km'],properties_json=js(props),source_ref='data/processed/overhead_segments.parquet'))
  if pd.notna(r['circuit_key']):relations.append(edge(r['circuit_key'],id,'SSEN_GIS','contains','published_GIS_circuit_id'))
  for lon,lat in [geom.coords[0],geom.coords[-1]]:
   key=f'SSEN:route_endpoint:{lon:.7f}:{lat:.7f}'
   junctions[key]=node(key,'junction','SSEN_GIS',longitude=lon,latitude=lat,geometry_wgs84_wkb=Point(lon,lat).wkb,location_method='published_route_endpoint',properties_json=js({'connection_status':'geometry_only_not_verified_electrical'}))
   edges.append(edge(id,key,'SSEN_GIS','endpoint_candidate','geometric_endpoint_coincidence_not_operator_connectivity'))
 save_nodes(rows+list(junctions.values()),'SSEN_routes');save_edges(edges,'SSEN_routes_candidate');save_edges(relations,'SSEN_circuit_membership')

def generation():
 rows=[];d=pd.read_csv(RAW/'repd_q2_2026.csv',encoding='cp1252',low_memory=False);d=d[d['Development Status'].astype(str).str.casefold().eq('operational') & ~d.Country.astype(str).str.contains('Northern Ireland',case=False)]
 for r in d.to_dict('records'):
  lon,lat,wkb=point(r.get('X-coordinate'),r.get('Y-coordinate'));id='REPD:'+str(r['Ref ID'])
  rows.append(node(id,'generation','REPD',source_asset_id=str(r['Ref ID']),operator=r.get('Operator (or Applicant)'),name=r.get('Site Name'),longitude=lon,latitude=lat,geometry_wgs84_wkb=wkb,location_method='published_project_coordinates' if wkb else None,generation_capacity_mw=num(r.get('Installed Capacity (MWelec)')),properties_json=js(r),source_ref='data/grid/raw/repd_q2_2026.csv'))
 save_nodes(rows,'REPD_generation')
 d=pd.read_excel(RAW/'dukes_power_stations_2026.xlsx',sheet_name='5.11 Full list',header=5);d=d[d['Site Name'].notna() & ~d.Country.astype(str).str.contains('Northern Ireland',case=False)];rows=[]
 for i,r in enumerate(d.to_dict('records')):
  lon,lat,wkb=point(r.get('X-Coordinate'),r.get('Y-Coordinate'));code=r.get('DESNZ site code');id='DUKES2026:'+str(code) if pd.notna(code) else 'DUKES2026:row:'+str(i)
  rows.append(node(id,'generation','DUKES2026',source_asset_id=str(code) if pd.notna(code) else 'row:'+str(i),operator=r.get('Company Name [note 30]'),name=r['Site Name'],longitude=lon,latitude=lat,geometry_wgs84_wkb=wkb,location_method='published_station_coordinates' if wkb else None,generation_capacity_mw=num(r.get('InstalledCapacity (MW)')),properties_json=js(r),source_ref='data/grid/raw/dukes_power_stations_2026.xlsx'))
 # Some official rows describe multiple units at one site; retain row identity instead of silently collapsing them.
 counts={}
 for row in rows:
  id=row['node_id'];counts[id]=counts.get(id,0)+1
  if counts[id]>1:row['node_id']=id+':record:'+str(counts[id])
 save_nodes(rows,'DUKES_generation')
 rows=[]
 for r in json.loads((RAW/'npg/embedded-capacity-register.json').read_text()):
  if str(r.get('connection_status')).casefold()!='connected':continue
  g=r.get('geopoint') or {};lon,lat=num(g.get('lon')),num(g.get('lat'))
  rows.append(node('NPG:ECR:'+str(r['ecrid']),'generation','NPG_ECR',source_asset_id=r['ecrid'],operator='Northern Powergrid',name=r.get('customer_site'),longitude=lon,latitude=lat,geometry_wgs84_wkb=Point(lon,lat).wkb if None not in (lon,lat) else None,location_method='published_connection_coordinates',generation_capacity_mw=num(r.get('already_connected_registered_capacity_mw')),voltage_kv=num(r.get('point_of_connection_poc_voltage_kv')),properties_json=js(r),source_ref='data/grid/raw/npg/embedded-capacity-register.json'))
 save_nodes(rows,'NPG_generation')
 rows=[]
 for path in (ROOT/'data/raw/embedded_capacity_register').glob('*.csv'):
  try:d=pd.read_csv(path,encoding='utf-8-sig',dtype=str,keep_default_na=False)
  except UnicodeDecodeError:continue
  d.columns=d.columns.str.strip()
  if 'Connection Status' not in d or 'Unique ID' not in d:continue
  for r in d[d['Connection Status'].str.upper().eq('CONNECTED')].to_dict('records'):
   lon,lat=num(r.get('Longitude')),num(r.get('Latitude'));rows.append(node('SSEN:ECR:'+r['Unique ID'],'generation','SSEN_ECR',source_asset_id=r['Unique ID'],operator=r.get('Licence Area'),name=r.get('Customer Site'),longitude=lon,latitude=lat,geometry_wgs84_wkb=Point(lon,lat).wkb if None not in (lon,lat) else None,location_method='published_connection_coordinates',generation_capacity_mw=num(r.get('Already connected Registered Capacity (MW)')),properties_json=js(r),source_ref=str(path.relative_to(ROOT))))
 save_nodes(rows,'SSEN_generation')
if __name__=='__main__':operator_inventories();generation();print('Located inventories and generation normalized',flush=True)
