"""Create a self-contained curated-data bundle; keep large source downloads in the raw archive."""
from pathlib import Path
import zipfile,json,hashlib
from grid_common import ROOT,BASE

def main():
 out=BASE/'packages/british_grid_graph.zip';out.parent.mkdir(exist_ok=True);files=[BASE/'british_grid.sqlite',BASE/'README.md',BASE/'ATTRIBUTION.md',BASE/'download_manifest.json',BASE/'curl_commands.sh',BASE/'requirements.lock']
 files+=sorted((BASE/'normalized').glob('*.parquet'));files+=sorted((BASE/'reports').glob('*.json'));files+=sorted((ROOT/'scripts').glob('grid_*.py'))
 with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3,allowZip64=True) as z:
  for path in files:z.write(path,str(path.relative_to(ROOT)))
 with zipfile.ZipFile(out) as z:
  bad=z.testzip()
  if bad:raise RuntimeError('Corrupt bundle member: '+bad)
 h=hashlib.sha256()
 with out.open('rb') as f:
  while block:=f.read(8*1024*1024):h.update(block)
 record={'path':str(out.relative_to(ROOT)),'bytes':out.stat().st_size,'sha256':h.hexdigest(),'members':len(files),'zip_integrity':'passed','original_raw_archives_included':False,'note':'Raw source archives remain local under data/grid/raw; download URLs and hashes are included.'};(BASE/'packages/package_manifest.json').write_text(json.dumps(record,indent=2));print(json.dumps(record,indent=2),flush=True)
if __name__=='__main__':main()
