"""Download public SSEN resources, with checksums and restartable local caching."""
import hashlib,json,pathlib,concurrent.futures,datetime
import requests
ROOT=pathlib.Path(__file__).resolve().parents[1]
RAW=ROOT/'data/raw'; RAW.mkdir(parents=True,exist_ok=True)
CORE={'nafirs-hv-faults','ssen_overhead_lines','ssen-substation-data','primary-supply-areas','secondary-supply-areas','bsp-gsp-supply-areas','ssen-distribution-s-data-glossary'}
def selected(d,r):
 f=r['format'].upper(); n=r['name'].lower()
 if d['name'] in CORE:return f in {'CSV','PDF'}
 if 'long_term_development_statement' in d['name']:return f in {'XLSX','CSV'} or (f=='ZIP' and ('existing' in n or 'eq profile' in n)) or (f=='PDF' and ('may 2026' in n or 'definition' in n or 'methodology' in n))
 if d['name']=='ssen_smart_meter_prod_lv_feeder':return ('bressay' in n and f in {'CSV','ZIP'})
 if d['name']=='nerda_opengrid_dashboard':return f=='PDF' and ('api guide' in n or 'definitions' in n)
 if d['name']=='embedded_capacity_register':return f=='CSV' or (f=='XLSX' and 'latest' not in n)
 if d['name']=='generation-availability-and-network-capacity':return f=='CSV'
 return False

def fetch(pair):
 d,r=pair; p=RAW/d['name']/(r['id']+'.'+r['format'].lower());p.parent.mkdir(exist_ok=True)
 row={'dataset':d['name'],'resource_id':r['id'],'name':r['name'],'url':r['url'],'license':d.get('license_id'),'source_modified':r.get('last_modified'),'path':str(p.relative_to(ROOT))}
 try:
  if not p.exists():
   res=requests.get(r['url'],timeout=180);res.raise_for_status();tmp=p.with_suffix('.part');tmp.write_bytes(res.content);tmp.replace(p)
  b=p.read_bytes();row.update(status='downloaded',bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),retrieved_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
  if r.get('sha256') and row['sha256']!=r['sha256']:raise ValueError('Publisher checksum mismatch')
 except Exception as e:row.update(status='failed',error=str(e))
 print(row['status'],r['name'],flush=True);return row
if __name__=='__main__':
 res=requests.get('https://data-api.ssen.co.uk/api/3/action/package_search',params={'rows':1000},timeout=60);res.raise_for_status();catalog=res.json()['result']['results'];(RAW/'ssen_catalog.json').write_text(json.dumps(catalog,indent=2))
 pairs=[(d,r) for d in catalog for r in d['resources'] if selected(d,r)]
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(fetch,pairs))
 (ROOT/'data/manifest.json').write_text(json.dumps(rows,indent=2))
 print('Resources',len(rows),'Bytes',sum(r.get('bytes',0) for r in rows))
