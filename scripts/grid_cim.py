"""Normalize existing-network CIM profiles into typed equipment nodes and terminal incidence edges."""
from pathlib import Path
import zipfile,io,json,xml.etree.ElementTree as ET,re,collections
import pandas as pd
from shapely.geometry import Point
from grid_common import ROOT,BASE,RAW,OUT,node,edge,save_nodes,save_edges,js,num
TYPES={'ConnectivityNode':'junction','TopologicalNode':'junction','Substation':'substation','WireSegment':'power_line','AsynchronousMachine':'generator_machine','StaticVarCompensator':'compensator','ACLineSegment':'power_line','PowerTransformer':'transformer','BusbarSection':'busbar','Breaker':'breaker','Disconnector':'disconnector','LoadBreakSwitch':'switch','DisconnectingCircuitBreaker':'breaker','GeneratingUnit':'generation','SynchronousMachine':'generator_machine','PowerElectronicsConnection':'inverter','EquivalentInjection':'supply_boundary','EnergyConsumer':'load_connection','LinearShuntCompensator':'compensator','SeriesCompensator':'compensator','EquivalentBranch':'equivalent_branch'}
def rid(v):return str(v).removeprefix('urn:uuid:').lstrip('#_') if v else None
def profiles(blob,label):
 with zipfile.ZipFile(io.BytesIO(blob)) as z:
  for name in z.namelist():
   low=name.lower()
   if low.endswith('.zip') and 'systemcapacity' in low and 'existing' in low:yield from profiles(z.read(name),label+'/'+name)
   elif low.endswith('.xml') and ('_eq' in low or low=='eq.xml' or '/eq.xml' in low):yield label+'/'+name,z.read(name)
def parse(blob):
 root=ET.fromstring(blob);objects={}
 for e in root:
  id=next((rid(v) for k,v in e.attrib.items() if k.endswith('}ID') or k.endswith('}about')),None)
  if id is None:continue
  props={}
  for child in e:
   ref=next((v for k,v in child.attrib.items() if k.endswith('}resource')),None)
   props[child.tag.split('}')[-1]]=rid(ref) if ref and (ref.startswith('#') or ref.startswith('urn:uuid:')) else ref if ref else child.text.strip() if child.text else None
  objects[id]={'class':e.tag.split('}')[-1],**props}
 return objects

def nged_locations():
 manifest=json.loads((BASE/'download_manifest.json').read_text());number_path=next((ROOT/r['path'] for r in manifest if 'Substation Numbers' in r['dataset'] and r['status']=='downloaded'),None)
 if number_path is None:return {}
 cross=pd.read_csv(number_path,dtype=str);sites=[]
 for r in manifest:
  if r['source']=='nged' and 'heatmap' in r['dataset'] and r['status']=='downloaded':sites+=json.loads((ROOT/r['path']).read_text())
 bynum={}
 for site in sites:
  loc=(num(site.get('longitude')),num(site.get('latitude')))
  if None not in loc:bynum[str(site.get('mRID'))]=(str(site.get('area','')).upper().replace(' ','_'),loc)
 candidates={}
 for _,r in cross.iterrows():
  name=str(r['LTDS Substation Name']).strip().casefold();hit=bynum.get(str(r['Substation Number']))
  if hit:
   region,loc=hit;candidates.setdefault(name,set()).add(loc);candidates.setdefault(region+':'+name,set()).add(loc)
 return {key:next(iter(locations)) for key,locations in candidates.items() if len(locations)==1}

def build(objects,layer,source_ref,locations):
 rows=[];edges=[];relations=[];nodes={};windings={}
 for end_id,end in objects.items():
  if end['class']=='PowerTransformerEnd':windings.setdefault(end.get('PowerTransformerEnd.PowerTransformer'),[]).append(end)
 ratings={}
 points_by_series={}
 for item in objects.values():
  for cls in ['CurrentLimit','ApparentPowerLimit']:
   if item['class']==cls+'DataPoint':points_by_series.setdefault(item.get(cls+'DataPoint.'+cls+'Series'),[]).append({'season':item.get('IdentifiedObject.name'),'rating':num(item.get(cls+'DataPoint.normalValue')),'unit':'A' if cls=='CurrentLimit' else 'MVA'})
 for limit_id,item in objects.items():
  cls=item['class']
  if cls not in ['CurrentLimit','ApparentPowerLimit']:continue
  limitset=objects.get(item.get('OperationalLimit.OperationalLimitSet'),{});terminal=objects.get(limitset.get('OperationalLimitSet.Terminal'),{});equipment=terminal.get('Terminal.ConductingEquipment');series=item.get(cls+'.'+cls+'Series')
  for point in points_by_series.get(series,[]):ratings.setdefault(equipment,[]).append(dict(point,limit_id=limit_id))
 rating_rows=[dict(equipment_id=layer+':'+str(equipment),**value) for equipment,values in ratings.items() for value in values]
 pd.DataFrame(rating_rows).to_parquet(OUT/(layer.replace(':','_')+'_seasonal_ratings.parquet'),index=False)
 voltage={k:num(p.get('BaseVoltage.nominalVoltage')) for k,p in objects.items() if p['class']=='BaseVoltage'}
 def station_of(id):
  visited=set()
  while id and id not in visited:
   visited.add(id);p=objects.get(id,{})
   if p.get('class')=='Substation':return id
   id=p.get('Equipment.EquipmentContainer') or p.get('ConnectivityNode.ConnectivityNodeContainer') or p.get('VoltageLevel.Substation') or p.get('Bay.VoltageLevel')
  return None
 for id,p in objects.items():
  cls=p['class']
  if cls not in TYPES and not cls.endswith('GeneratingUnit'):continue
  name=p.get('IdentifiedObject.name');kwargs={};station=station_of(id)
  if id in ratings:p=dict(p,seasonal_ratings=ratings[id])
  if cls=='PowerTransformer':
   p=dict(p,windings=windings.get(id,[]));ratings={num(x.get('PowerTransformerEnd.ratedS')) for x in windings.get(id,[])}-{None}
   if len(ratings)==1:kwargs['capacity_mva']=next(iter(ratings))
  if station and layer.startswith('NGED'):
   stationname=objects[station].get('IdentifiedObject.name','').strip().casefold();loc=locations.get(layer.split(':')[-1]+':'+stationname,locations.get(stationname))
   if loc and cls!='ACLineSegment':kwargs.update(longitude=loc[0],latitude=loc[1],geometry_wgs84_wkb=Point(*loc).wkb,location_method='documented_NGED_substation_number_crosswalk' if cls=='Substation' else 'located_parent_substation_point')
  bv=p.get('ConductingEquipment.BaseVoltage');v=voltage.get(bv)
  if v is None:
   vl=objects.get(p.get('Equipment.EquipmentContainer'),{})
   v=voltage.get(vl.get('VoltageLevel.BaseVoltage'))
  kwargs.update(voltage_kv=v,r_ohm=num(p.get('ACLineSegment.r')),x_ohm=num(p.get('ACLineSegment.x')),b_siemens=num(p.get('ACLineSegment.bch')),length_km=num(p.get('Conductor.length')),generation_capacity_mw=num(p.get('GeneratingUnit.ratedNetMaxP')))
  kwargs['electrical_parameters_status']='published_CIM' if cls in ['ACLineSegment','GeneratingUnit','PowerTransformer'] else 'see_source_properties'
  row=node(layer+':'+id,TYPES.get(cls,'generation'),layer,source_asset_id=id,operator=layer.split(':')[0],name=name,topology_layer=layer,source_ref=source_ref,properties_json=js(p),**kwargs);rows.append(row);nodes[id]=row
  if station and station!=id:relations.append(edge(layer+':'+station,layer+':'+id,layer,'contains','published_CIM_container_chain'))
 unresolved=[]
 for id,p in objects.items():
  if p['class']!='Terminal':continue
  equipment=p.get('Terminal.ConductingEquipment');bus=p.get('Terminal.ConnectivityNode') or p.get('Terminal.TopologicalNode')
  if equipment in nodes and bus in nodes:edges.append(edge(layer+':'+equipment,layer+':'+bus,layer,'electrical_terminal','published_CIM_terminal'))
  else:unresolved.append({'terminal_id':id,'equipment_id':equipment,'node_id':bus})
 save_nodes(rows,layer.replace(':','_'));save_edges(edges,layer.replace(':','_'));save_edges(relations,layer.replace(':','_')+'_containment')
 props=[{'object_id':id,'class':p['class'],'properties_json':js(p)} for id,p in objects.items()];pd.DataFrame(props).to_parquet(OUT/(layer.replace(':','_')+'_all_properties.parquet'),index=False)
 return {'layer':layer,'nodes':len(rows),'edges':len(edges),'located_nodes':sum(r['latitude'] is not None for r in rows),'classes':dict(collections.Counter(p['class'] for p in objects.values())),'unresolved_terminals':unresolved}
def main():
 locations=nged_locations();reports=[];manifest=json.loads((BASE/'download_manifest.json').read_text())
 for r in manifest:
  if r['status']!='downloaded' or not r['path'].endswith('.zip'):continue
  path=ROOT/r['path']
  if r['source']=='nged' and 'CIM ' in r['dataset']:
   region=r['dataset'].split('CIM ')[-1].replace(' ','_').upper();layer='NGED:'+region
  elif r['source']=='spen' and 'systemcapacity_existing' in path.name.lower():layer='SPEN:'+('SPM' if 'spm' in path.name else 'SPD')
  else:continue
  for label,blob in profiles(path.read_bytes(),path.name):
   print('Parsing',layer,label,flush=True);reports.append(build(parse(blob),layer,r['path'],locations))
 # Reuse the already downloaded SSEN EQ profiles, preserving the two operator regions.
 original=json.loads((ROOT/'data/manifest.json').read_text())
 for r in original:
  if 'System Capacity Existing' not in r['name']:continue
  layer='SSEN:'+('SHEPD' if r['name'].startswith('SHEPD') else 'SEPD')
  for label,blob in profiles((ROOT/r['path']).read_bytes(),r['path']):reports.append(build(parse(blob),layer,r['path'],{}))
 (BASE/'reports/cim_report.json').write_text(json.dumps(reports,indent=2));print([(r['layer'],r['nodes'],r['edges']) for r in reports],flush=True)
if __name__=='__main__':main()
