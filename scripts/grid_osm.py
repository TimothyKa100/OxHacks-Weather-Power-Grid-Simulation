"""Extract national OSM power assets and original node references without inventing connectivity."""
from pathlib import Path
import json,gzip,hashlib,collections
import osmium,pandas as pd,numpy as np,shapely
from shapely.geometry import Point,LineString,Polygon
from shapely.ops import polygonize,unary_union
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'data/grid';RAW=BASE/'raw';OUT=BASE/'normalized';OUT.mkdir(exist_ok=True)
PBF=RAW/'great-britain-261023.osm.pbf'
def main():
 cache=RAW/'osm_power_entities.jsonl.gz'
 if not cache.exists():
  count=0
  with gzip.open(cache.with_suffix('.part'),'wt') as out:
   for o in osmium.FileProcessor(PBF).with_filter(osmium.filter.KeyFilter('power')):
    tags=dict(o.tags);r={'osm_id':o.id,'entity_type':'node' if o.is_node() else 'way' if o.is_way() else 'relation','tags':tags,'osm_timestamp':str(o.timestamp)}
    if o.is_node():r.update(longitude=o.location.lon,latitude=o.location.lat)
    elif o.is_way():r['node_ids']=[n.ref for n in o.nodes]
    else:r['members']=[{'type':m.type,'ref':m.ref,'role':m.role} for m in o.members]
    out.write(json.dumps(r)+'\n');count+=1
    if count%25000==0:print('Tagged power objects',count,flush=True)
  cache.with_suffix('.part').replace(cache);print('Power objects saved',count,flush=True)
 def records():
  with gzip.open(cache,'rt') as f:
   for line in f:yield json.loads(line)
 ways={r['osm_id']:r['node_ids'] for r in records() if r['entity_type']=='way'}
 relations=[r for r in records() if r['entity_type']=='relation']
 needed_ways={m['ref'] for r in relations for m in r.get('members',[]) if m['type']=='w'}-set(ways)
 print('Resolving relation ways',len(needed_ways),flush=True)
 if needed_ways:
  for w in osmium.FileProcessor(PBF,entities=osmium.osm.WAY).with_filter(osmium.filter.IdFilter(needed_ways)):ways[w.id]=[n.ref for n in w.nodes]
 needed_nodes={n for ids in ways.values() for n in ids}
 needed_nodes.update(m['ref'] for r in relations for m in r.get('members',[]) if m['type']=='n')
 coords={};print('Resolving coordinates',len(needed_nodes),flush=True)
 for n in osmium.FileProcessor(PBF,entities=osmium.osm.NODE).with_filter(osmium.filter.IdFilter(needed_nodes)):coords[n.id]=(n.location.lon,n.location.lat)
 pd.DataFrame({'osm_node_id':list(coords),'longitude':[v[0] for v in coords.values()],'latitude':[v[1] for v in coords.values()]}).to_parquet(OUT/'osm_referenced_coordinates.parquet',index=False)
 import pyarrow as pa,pyarrow.parquet as pq
 schema=pa.schema([('asset_id',pa.string()),('source',pa.string()),('source_asset_id',pa.string()),('power_type',pa.string()),('name',pa.string()),('operator',pa.string()),('geometry_wgs84_wkb',pa.binary()),('longitude',pa.float64()),('latitude',pa.float64()),('node_ids_json',pa.string()),('members_json',pa.string()),('tags_json',pa.string()),('lifecycle_status',pa.string()),('snapshot',pa.string()),('osm_last_edit',pa.string())])
 writer=pq.ParquetWriter(OUT/'osm_power_assets.parquet',schema,compression='zstd');rows=[];count=0;located=0;types=collections.Counter()
 for r in records():
  typ=r['entity_type'];tags=r['tags'];power=tags['power'];geom=None;ids=r.get('node_ids',[])
  if typ=='node':geom=Point(r['longitude'],r['latitude'])
  elif typ=='way':
   pts=[coords[n] for n in ids if n in coords]
   if len(pts)==len(ids) and len(pts)>=2:
    geom=Polygon(pts) if len(pts)>=4 and ids[0]==ids[-1] and power not in ['line','minor_line','cable','busbar','bay'] else LineString(pts)
  else:
   lines=[]
   for m in r.get('members',[]):
    ns=ways.get(m['ref']) if m['type']=='w' else None
    if ns and all(n in coords for n in ns) and len(ns)>1:lines.append(LineString([coords[n] for n in ns]))
   if lines:
    polygons=list(polygonize(lines));geom=unary_union(polygons) if polygons else unary_union(lines)
  if geom is not None and not geom.is_empty:
   geom=shapely.make_valid(geom);point=geom.representative_point();lon,lat=point.x,point.y
  else:lon=lat=None
  lifecycle='existing_as_mapped'
  if power in ['construction','proposed','planned','disused','abandoned','demolished'] or any(tags.get(k) not in [None,'no'] for k in ['construction','proposed','disused','abandoned','demolished']):lifecycle='non_operational_or_uncertain'
  rows.append({'asset_id':f'OSM:{typ}:{r["osm_id"]}','source':'OpenStreetMap','source_asset_id':f'{typ}/{r["osm_id"]}','power_type':power,'name':tags.get('name'),'operator':tags.get('operator'),'geometry_wgs84_wkb':geom.wkb if geom is not None else None,'longitude':lon,'latitude':lat,'node_ids_json':json.dumps(ids),'members_json':json.dumps(r.get('members',[])),'tags_json':json.dumps(tags),'lifecycle_status':lifecycle,'snapshot':'2026-10-23','osm_last_edit':r['osm_timestamp']})
  count+=1;located+=lon is not None;types[power]+=1
  if len(rows)>=25000:writer.write_table(pa.Table.from_pylist(rows,schema=schema));rows=[];print('Geometries normalized',count,flush=True)
 if rows:writer.write_table(pa.Table.from_pylist(rows,schema=schema))
 writer.close()
 report={'power_objects':count,'power_types':dict(types),'located_objects':located,'referenced_coordinates':len(coords),'unresolved_node_references':sum(n not in coords for ns in ways.values() for n in ns),'source':'https://download.geofabrik.de/europe/great-britain.html','snapshot':'2026-10-23','license':'ODbL-1.0','attribution':'© OpenStreetMap contributors; extract by Geofabrik','coverage':'Great Britain; mapped inventory is incomplete and is not an operator-validated electrical model'}
 (BASE/'reports/osm_report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2),flush=True)
if __name__=='__main__':main()
