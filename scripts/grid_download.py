"""Fetch public grid supplements through curl; retain requests, checksums and access failures."""
from pathlib import Path
import json,hashlib,subprocess,datetime,concurrent.futures,urllib.parse,shlex
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'data/grid';RAW=BASE/'raw';RAW.mkdir(parents=True,exist_ok=True)
MANIFEST=BASE/'download_manifest.json';ROWS=json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
def fetch(url,path,source,dataset,license=None,max_time=600):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 old=next((r for r in ROWS if r['path']==str(path.relative_to(ROOT)) and r.get('status')=='downloaded'),None)
 if old and path.exists() and path.stat().st_size==old.get('bytes'):return old
 row={'url':url,'path':str(path.relative_to(ROOT)),'source':source,'dataset':dataset,'license':license,'retrieved_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 tmp=path.with_suffix(path.suffix+'.part');cmd=['curl','--silent','--show-error','--location','--retry','2','--connect-timeout','25','--max-time',str(max_time),'--output',str(tmp),'--write-out','%{http_code}',url]
 row['curl_command']=shlex.join(cmd)
 try:
  r=subprocess.run(cmd,capture_output=True,text=True,timeout=max_time*3+60);row['http_status']=int(r.stdout[-3:]) if r.stdout[-3:].isdigit() else None
  if r.returncode or row['http_status']!=200:
   row.update(status='access_blocked' if row['http_status'] in (401,403) else 'failed',error=r.stderr[-1000:])
   if tmp.exists():row['response_excerpt']=tmp.read_bytes()[:500].decode(errors='replace');tmp.unlink()
  else:
   tmp.replace(path);row.update(status='downloaded',bytes=path.stat().st_size,sha256=hashlib.file_digest(path.open('rb'),'sha256').hexdigest() if hasattr(hashlib,'file_digest') else hashlib.sha256(path.read_bytes()).hexdigest())
 except Exception as e:row.update(status='failed',error=str(e))
 if row.get('status')=='downloaded' and '/exports/' in url and path.suffix=='.json':
  try:
   if json.loads(path.read_text())==[]:row.update(status='empty_export_unavailable',note='Catalogue advertises records but anonymous export returned no records.')
  except (ValueError,UnicodeDecodeError):row.update(status='invalid_response')
 print(row['status'],source,dataset,flush=True);return row

def merge(rows):
 global ROWS
 ROWS=list({r['path']:r for r in ROWS+rows}.values());MANIFEST.write_text(json.dumps(ROWS,indent=2))
def catalog_ods(source,host):
 combined=[]
 for offset in range(0,2000,100):
  path=RAW/source/f'catalog_{offset}.json';r=fetch(f'https://{host}/api/explore/v2.1/catalog/datasets?limit=100&offset={offset}',path,source,'catalogue');merge([r])
  if r['status']!='downloaded':break
  j=json.loads(path.read_text());combined+=j['results']
  if len(combined)>=j['total_count']:break
 (RAW/source/'catalog.json').write_text(json.dumps(combined,indent=2));return combined

def main():
 nged=json.loads((RAW/'nged_catalog.json').read_text())['result']['results'];jobs=[]
 for d in nged:
  if d['name'] not in ['ltds-common-information-model','ltds-tabular-model','ltds-capacity-heatmap']:continue
  for r in d['resources']:
   if r['url']=='redacted':continue
   ext=r.get('format','bin').lower();jobs.append((r['url'],RAW/'nged'/d['name']/(r['id']+'.'+ext),'nged',d['name']+':'+r['name'],d.get('license_id')))
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:merge(list(pool.map(lambda a:fetch(*a),jobs)))
 for source,host in [('npg','northernpowergrid.opendatasoft.com'),('spen','spenergynetworks.opendatasoft.com'),('ssen_transmission','ssentransmission.opendatasoft.com'),('ukpn','ukpowernetworks.opendatasoft.com'),('enw','electricitynorthwest.opendatasoft.com')]:
  catalog=catalog_ods(source,host)
  for d in catalog:
   id=d['dataset_id']
   selected=(source=='npg' and (id in ['substation_sites_list','ltds_ehv_sites','ltds-cim-files','ltds-zips','npg-ehv-overhead-feeders','npg-hv-overhead-feeders','npg-hv-underground-cables','lv-underground-cables','npg-lv-overhead-feeders','embedded-capacity-register'] or id.startswith('ltds-appendix-')))
   probe={'spen':'spd-ltds-appendix-1-circuit-data-table-1','ssen_transmission':'overhead-line-grid','ukpn':'grid-and-primary-sites','enw':'sp-enw-substation-hierarchy'}.get(source)
   if source!='npg':selected=id==probe
   if selected:
    r=fetch(f'https://{host}/api/explore/v2.1/catalog/datasets/{id}/exports/json',RAW/source/(id+'.json'),source,id,d.get('metas',{}).get('default',{}).get('license'));merge([r])
 # File-backed public CIM resources referenced by NPG's catalogue records.
 path=RAW/'npg/ltds-cim-files.json'
 if path.exists():
  jobs=[]
  for item in json.loads(path.read_text()):
   for field in ['existing_fault_level_case_model','existing_system_capacity_case_model']:
    f=item.get(field)
    if isinstance(f,dict) and f.get('url'):jobs.append((f['url'],RAW/'npg/cim'/f"{item.get('file_idesc',item.get('name','file'))}_{field}.zip",'npg','ltds-cim-files:'+field,None))
  with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:merge(list(pool.map(lambda a:fetch(*a),jobs)))
 print('Manifest entries',len(ROWS),flush=True)
if __name__=='__main__':main()
