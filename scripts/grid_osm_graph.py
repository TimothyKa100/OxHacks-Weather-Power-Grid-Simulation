"""Create asset--line-node--asset incidence, with voltage-aware mapped junctions and explicit uncertainty."""
import json,collections,re
import pandas as pd,numpy as np,shapely
from shapely.geometry import Point,LineString
from shapely.ops import transform
from pyproj import Transformer
from grid_common import BASE,OUT,node,edge,save_nodes,save_edges,stream_nodes,js,num
CONDUCTORS={'line','minor_line','cable','busbar','bay'}
TYPE={'plant':'generation','generator':'distributed_generator','substation':'substation','transformer':'transformer','switch':'switch','tower':'tower','pole':'pole','terminal':'terminal','converter':'converter','compensator':'compensator','connection':'junction'}
def volts(tags):
 vals=[]
 for token in str(tags.get('voltage','')).split(';'):
  try:vals.append(float(token)/1000)
  except ValueError:pass
 return sorted(set(vals))
def capacity(tags):
 text=tags.get('plant:output:electricity',tags.get('generator:output:electricity',''));m=re.fullmatch(r'([\d.]+)\s*(kW|MW|GW)',str(text),re.I)
 return float(m[1])*{'kw':.001,'mw':1,'gw':1000}[m[2].lower()] if m else None

def main():
 core=sorted(CONDUCTORS|{'plant','substation','transformer','switch','terminal','converter','compensator','connection'})
 d=pd.read_parquet(OUT/'osm_power_assets.parquet',filters=[('power_type','in',core)]);active=d[d.lifecycle_status.eq('existing_as_mapped')];excluded=pd.read_parquet(OUT/'osm_power_assets.parquet',filters=[('lifecycle_status','!=','existing_as_mapped')]);excluded.to_parquet(OUT/'osm_non_operational_or_uncertain.parquet',index=False)
 import pyarrow.parquet as pq
 def other_assets():
  for batch in pq.ParquetFile(OUT/'osm_power_assets.parquet').iter_batches(batch_size=25000):
   for r in batch.to_pylist():
    if r['power_type'] in core or r['lifecycle_status']!='existing_as_mapped':continue
    tags=json.loads(r['tags_json']);v=volts(tags)
    yield node(r['asset_id'],TYPE.get(r['power_type'],'other_power_equipment'),'OSM',source_asset_id=r['source_asset_id'],operator=r['operator'],name=r['name'],longitude=r['longitude'],latitude=r['latitude'],geometry_wgs84_wkb=r['geometry_wgs84_wkb'],location_method='mapped_point_or_geometry',voltage_kv=max(v) if v else None,generation_capacity_mw=capacity(tags),properties_json=r['tags_json'],source_ref='data/grid/normalized/osm_power_assets.parquet')
 print('Streaming support and generator assets',flush=True);stream_nodes(other_assets(),'OSM_other_assets')
 coordinates=pd.read_parquet(OUT/'osm_referenced_coordinates.parquet');coords={int(r.osm_node_id):(r.longitude,r.latitude) for r in coordinates.itertuples()};metadata=pd.read_parquet(OUT/'osm_power_assets.parquet',columns=['source_asset_id','power_type','lifecycle_status']);tagged={int(r.source_asset_id.split('/')[1]):r.power_type for r in metadata.itertuples() if r.source_asset_id.startswith('node/') and r.lifecycle_status=='existing_as_mapped'};del metadata
 lines=active[active.power_type.isin(CONDUCTORS)&active.source_asset_id.str.startswith('way/')];usage=collections.Counter(n for s in lines.node_ids_json for n in set(json.loads(s)));rows=[];edges=[];relations=[];junctions={};asset_ids=set();forward=Transformer.from_crs(4326,27700,always_xy=True)
 for r in active[~active.index.isin(lines.index)].itertuples():
  tags=json.loads(r.tags_json);v=volts(tags);row=node(r.asset_id,TYPE.get(r.power_type,'other_power_equipment'),'OSM',source_asset_id=r.source_asset_id,operator=r.operator,name=r.name,longitude=r.longitude,latitude=r.latitude,geometry_wgs84_wkb=r.geometry_wgs84_wkb,location_method='mapped_point' if r.source_asset_id.startswith('node/') else 'mapped_geometry_representative_point',voltage_kv=max(v) if v else None,generation_capacity_mw=capacity(tags),properties_json=r.tags_json,source_ref='data/grid/normalized/osm_power_assets.parquet');rows.append(row);asset_ids.add(r.asset_id)
 def connector(ref,wayid,signature):
  pt=coords[ref];power=tagged[ref] if ref in tagged else None
  # A tower is a mechanical support, not a bus joining every circuit carried by it.
  if power in ['tower','pole']:key=f'OSM:route_port:{wayid}:{ref}'
  else:key=f'OSM:junction:{ref}:voltage:{signature}'
  if key not in junctions:junctions[key]=node(key,'junction','OSM',source_asset_id='node/'+str(ref),longitude=pt[0],latitude=pt[1],geometry_wgs84_wkb=Point(*pt).wkb,location_method='mapped_route_vertex',properties_json=js({'osm_node_id':ref,'voltage_signature_kv':signature,'electrical_certification':'mapped_only_not_operator_verified'}))
  if ref in tagged:relations.append(edge(f'OSM:node:{ref}',key,'OSM','supports_or_contains_terminal','shared_OSM_vertex_not_electrical_identity'))
  return key
 for i,r in enumerate(lines.itertuples()):
  refs=json.loads(r.node_ids_json)
  if len(refs)<2 or any(n not in coords for n in refs):continue
  tags=json.loads(r.tags_json);v=volts(tags);signature='_'.join(map(str,v)) or 'unknown'
  cuts=[0]+[j for j,n in enumerate(refs[1:-1],1) if usage[n]>1 or n in tagged and tagged[n] in ['switch','transformer','connection','terminal']]+[len(refs)-1]
  for part,(a,b) in enumerate(zip(cuts,cuts[1:])):
   geom=LineString([coords[n] for n in refs[a:b+1]]);id=r.asset_id+f':section:{part}';point=geom.interpolate(.5,normalized=True);props=dict(tags,osm_way_id=r.source_asset_id,section=part,original_voltage_values_kv=v,geometry_kind='mapped_route',electrical_certification='mapped_corridor_not_operator_validated_circuit');length=transform(forward.transform,geom).length/1000
   rows.append(node(id,'power_line','OSM',source_asset_id=r.source_asset_id,operator=r.operator,name=r.name,longitude=point.x,latitude=point.y,geometry_wgs84_wkb=geom.wkb,location_method='mapped_route',voltage_kv=max(v) if v else None,length_km=length,properties_json=js(props),source_ref='data/grid/normalized/osm_power_assets.parquet'))
   for ref in [refs[a],refs[b]]:edges.append(edge(id,connector(ref,r.source_asset_id,signature),'OSM','line_terminal','mapped_shared_vertex_voltage_compatible'))
   for ref in refs[a:b+1]:
    if ref in tagged:relations.append(edge(f'OSM:node:{ref}',id,'OSM','route_support_or_equipment_member','shared_OSM_node_id'))
  if i and i%10000==0:print('OSM conductor ways processed',i,flush=True)
 nodes=save_nodes(rows+list(junctions.values()),'OSM');save_edges(edges,'OSM');save_edges(relations,'OSM_membership')
 # Geographic site membership does not establish a conducting electrical connection.
 sites=nodes[nodes.node_type.isin(['substation','generation'])&nodes.geometry_wgs84_wkb.notna()];geoms=shapely.from_wkb(sites.geometry_wgs84_wkb.to_numpy());mask=np.isin(shapely.get_type_id(geoms),[3,6]);geoms=geoms[mask];ids=sites.node_id.to_numpy()[mask]
 if len(geoms):
  tree=shapely.STRtree(geoms);points=shapely.points(nodes.longitude.to_numpy(dtype=float),nodes.latitude.to_numpy(dtype=float));pairs=tree.query(points,predicate='within');contain=[]
  for point_index,site_index in zip(*pairs):
   a,b=ids[site_index],nodes.node_id.iloc[point_index]
   if a!=b:contain.append(edge(a,b,'OSM','within_site_footprint','spatial_membership_not_electrical_connection'))
  save_edges(contain,'OSM_site_membership')
 print('OSM graph',len(nodes),'nodes',len(edges),'incidences',flush=True)
if __name__=='__main__':main()
