"""Offline OS Terrain 50 sampling in EPSG:27700; terrain height is not conductor height."""
from pathlib import Path
import zipfile,io,json,functools
import numpy as np,pandas as pd,shapely
from pyproj import Transformer
from grid_common import BASE,RAW,OUT
class Terrain50:
 def __init__(self,path=None):
  self.zip=zipfile.ZipFile(path or RAW/'os_terrain50_gb.zip');index_path=BASE/'reports/terrain_tile_index.json'
  if index_path.exists():self.index={int(k):v for k,v in json.loads(index_path.read_text()).items()}
  else:
   self.index={}
   for name in self.zip.namelist():
    if not name.endswith('.zip'):continue
    with zipfile.ZipFile(io.BytesIO(self.zip.read(name))) as child:
     asc=next(n for n in child.namelist() if n.lower().endswith('.asc'))
     with child.open(asc) as f:
      header={}
      for i in range(5):
       k,v=f.readline().decode().split();header[k.lower()]=float(v)
     key=int(header['xllcorner']//10000)*1000+int(header['yllcorner']//10000);self.index[key]=dict(zip_member=name,asc=asc,**header)
   index_path.write_text(json.dumps(self.index,indent=2))
  self.transform=Transformer.from_crs(4326,27700,always_xy=True)
 @functools.lru_cache(maxsize=128)
 def tile(self,key):
  meta=self.index[key]
  with zipfile.ZipFile(io.BytesIO(self.zip.read(meta['zip_member']))) as z:
   raw=z.read(meta['asc']);lines=raw.splitlines();skip=5;nodata=None
   if len(lines[5].split())==2 and lines[5].lower().startswith(b'nodata'):
    nodata=float(lines[5].split()[1]);skip=6
   array=np.loadtxt(io.BytesIO(b'\n'.join(lines[skip:])),dtype=np.float32)
  if nodata is not None:array[array==nodata]=np.nan
  return meta,array
 def sample_xy(self,x,y):
  x=np.asarray(x,dtype=float);y=np.asarray(y,dtype=float);result=np.full(x.shape,np.nan);valid=np.isfinite(x)&np.isfinite(y);positions=np.flatnonzero(valid)
  if not len(positions):return result
  keys=(np.floor(x[positions]/10000).astype(int)*1000+np.floor(y[positions]/10000).astype(int));order=np.argsort(keys);positions=positions[order];keys=keys[order];unique,starts=np.unique(keys,return_index=True)
  for j,key in enumerate(unique):
   if key not in self.index:continue
   idx=positions[starts[j]:starts[j+1] if j+1<len(starts) else len(positions)];m,a=self.tile(int(key));cols=np.floor((x[idx]-m['xllcorner'])/m['cellsize']).astype(int);rows=int(m['nrows'])-1-np.floor((y[idx]-m['yllcorner'])/m['cellsize']).astype(int);ok=(cols>=0)&(cols<a.shape[1])&(rows>=0)&(rows<a.shape[0]);result[idx[ok]]=a[rows[ok],cols[ok]]
  return result
 def sample_lonlat(self,lon,lat):return self.sample_xy(*self.transform.transform(lon,lat))

def enrich(path,spacing=250.):
 d=pd.read_parquet(path);d=d.drop(columns=[c for c in d if c.startswith('terrain_') or c in ['elevation_source','elevation_resolution_m','elevation_kind']]);terrain=Terrain50();d['terrain_elevation_m']=terrain.sample_lonlat(d.longitude.to_numpy(),d.latitude.to_numpy());d['elevation_source']='OS Terrain 50 GB 2026';d['elevation_resolution_m']=50.;d['elevation_kind']='ground_terrain_not_equipment_height'
 # Sample every line part separately. Every part includes endpoints; other assets get their representative-point value.
 profile=[];forward=Transformer.from_crs(4326,27700,always_xy=True);back=Transformer.from_crs(27700,4326,always_xy=True)
 from shapely.ops import transform
 for row in d[d.node_type.eq('power_line') & d.geometry_wgs84_wkb.notna()].itertuples():
  geom=shapely.from_wkb(row.geometry_wgs84_wkb)
  parts=list(geom.geoms) if geom.geom_type=='MultiLineString' else [geom] if geom.geom_type=='LineString' else []
  for part_no,part in enumerate(parts):
   g=transform(forward.transform,part);n=max(2,int(np.ceil(g.length/spacing))+1);distance=np.linspace(0,g.length,n);pts=shapely.line_interpolate_point(g,distance)
   for dist,x,y in zip(distance,shapely.get_x(pts),shapely.get_y(pts)):profile.append((row.node_id,part_no,float(dist),x,y))
 if profile:
  p=pd.DataFrame(profile,columns=['node_id','part','distance_m','easting','northing']);p['terrain_elevation_m']=terrain.sample_xy(p.easting.to_numpy(),p.northing.to_numpy());p['elevation_source']='OS Terrain 50 2026';p.to_parquet(path.with_name(path.stem.replace('_nodes','')+'_elevation_profiles.parquet'),index=False)
  stats=p.groupby('node_id').terrain_elevation_m.agg(['min','max','mean','count','size']).rename(columns={'min':'terrain_min_m','max':'terrain_max_m','mean':'terrain_mean_m','count':'terrain_valid_samples','size':'terrain_requested_samples'});d=d.merge(stats,on='node_id',how='left',validate='one_to_one');d['terrain_profile_coverage']=d.terrain_valid_samples/d.terrain_requested_samples
 d.to_parquet(path,index=False);print(path.name,'elevations',int(d.terrain_elevation_m.notna().sum()),'/',len(d),'line profile samples',len(profile),flush=True)
if __name__=='__main__':
 import argparse
 ap=argparse.ArgumentParser();ap.add_argument('files',nargs='*');args=ap.parse_args()
 for path in [Path(x) for x in args.files] if args.files else sorted(OUT.glob('*_nodes.parquet')):enrich(path)
