"""Elexon asset-identified generation/interconnector unavailability archive.

This is separate from distribution customer power cuts. A REMIT message may
describe maintenance, partial derating, a cancellation or an updated forecast.
It is not automatically a physically broken asset or a customer interruption.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timezone
import hashlib
import json
from pathlib import Path
import time
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/grid/raw/outages/elexon_remit'
BASE='https://data.elexon.co.uk/bmrs/api/v1'


def get(path,params,target):
    if target.exists(): return json.loads(target.read_text())
    for attempt in range(3):
        try:
            response=requests.get(BASE+path,params=params,timeout=(20,180));response.raise_for_status()
            data=response.json();target.write_bytes(response.content);return data
        except requests.RequestException:
            if attempt==2:raise
            time.sleep(2**attempt)


def main():
    OUT.mkdir(parents=True,exist_ok=True);today=date.today();identifiers={}
    def year(y):
        finish=min(date(y+1,1,1),today)
        data=get('/remit/list/by-event/stream',{'from':f'{y}-01-01','to':finish.isoformat()},OUT/f'event_index_{y}.json')
        print(y,len(data),flush=True);return data
    with ThreadPoolExecutor(3) as pool:
        for data in pool.map(year,range(2015,today.year+1)):
            for item in data:identifiers[item['id']]=item
    ids=sorted(identifiers);print('Unique report revisions',len(ids),flush=True)
    # The API accepts repeated messageId parameters; keep requests modest.
    def details(batch):
        target=OUT/f'details_{batch[0]}_{batch[-1]}.json'
        return get('/remit',[('messageId',x) for x in batch]+[('format','json')],target)
    total=0
    with ThreadPoolExecutor(3) as pool:
        for i,data in enumerate(pool.map(details,[ids[j:j+400] for j in range(0,len(ids),400)])):
            total+=len(data.get('data',[]))
            if i%20==0:print('Detailed revisions',total,flush=True)
    files=[dict(path=str(p.relative_to(ROOT)),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(OUT.glob('*.json')) if p.name!='manifest.json']
    (OUT/'manifest.json').write_text(json.dumps(dict(retrieved_utc=datetime.now(timezone.utc).isoformat(),
        source=BASE,scope='Events overlapping 2015-01-01 through collection date; latest available revisions from each query',
        indexed_revisions=len(ids),detailed_revisions=total,
        licence='Elexon BMRS Data Licence; https://www.elexon.co.uk/operations-settlement/bsc-central-services/bmrs/',
        interpretation='Generation/interconnector unavailability reports, separate from customer power cuts; revisions are not independent outages.',files=files),indent=2))
    print('Complete',total,flush=True)


if __name__=='__main__':main()
