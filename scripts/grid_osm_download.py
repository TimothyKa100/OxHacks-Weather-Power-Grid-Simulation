"""Resume a Geofabrik extract with three bounded HTTP ranges, then verify its publisher checksum."""
from pathlib import Path
import subprocess,concurrent.futures,hashlib,json,datetime
ROOT=Path(__file__).resolve().parents[1];RAW=ROOT/'data/grid/raw';P=RAW/'great-britain-261023.osm.pbf';URL='https://download.geofabrik.de/europe/great-britain-261023.osm.pbf';SIZE=2192208243
if __name__=='__main__':
 subprocess.run(['curl','--fail','--silent','--show-error','--location',URL+'.md5','-o',str(P)+'.md5'],check=True)
 expected=Path(str(P)+'.md5').read_text().split()[0];start=P.stat().st_size if P.exists() else 0
 if start<SIZE:
  remaining=SIZE-start;step=(remaining+2)//3;jobs=[(i,a,min(a+step-1,SIZE-1)) for i,a in enumerate(range(start,SIZE,step))]
  def part(job):
   i,a,b=job;q=Path(str(P)+f'.range_{a}_{b}');cmd=['curl','--fail','--silent','--show-error','--location','--retry','2','--connect-timeout','25','--max-time','1200','--range',f'{a}-{b}',URL,'-o',str(q)]
   if not q.exists() or q.stat().st_size!=b-a+1:subprocess.run(cmd,check=True)
   if q.stat().st_size!=b-a+1:raise RuntimeError('Range length mismatch')
   print('Range downloaded',i,q.stat().st_size,flush=True);return q
  with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:parts=list(pool.map(part,jobs))
  complete=P.with_suffix('.complete')
  with complete.open('wb') as out:
   for source in [P]+parts:
    with source.open('rb') as f:
     while block:=f.read(8*1024*1024):out.write(block)
  complete.replace(P)
 h=hashlib.md5();sha=hashlib.sha256()
 with P.open('rb') as f:
  while block:=f.read(8*1024*1024):h.update(block);sha.update(block)
 if h.hexdigest()!=expected:raise RuntimeError('Publisher MD5 mismatch')
 (RAW/'osm_download_verified.json').write_text(json.dumps({'url':URL,'bytes':P.stat().st_size,'md5':h.hexdigest(),'sha256':sha.hexdigest(),'license':'ODbL-1.0','retrieved_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()},indent=2));print('National OSM checksum verified',flush=True)
