"""Download public NI outage schedules and regional validation evidence.

Published maintenance plans are archived separately from actual fault reports.
No account registration or restricted API access is attempted.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin

import requests

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/grid/raw/research'


def page_data(path):
    text=path.read_text()
    match=re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',text)
    return json.loads(match[1]) if match else json.loads(text)


def documents(value):
    found=set()
    def walk(v):
        if isinstance(v,dict):
            if 'filename' in v and isinstance(v.get('uri'),dict):found.add((v['filename'],v['uri'].get('url')))
            for x in v.values():walk(x)
        elif isinstance(v,list):
            for x in v:walk(x)
    walk(value)
    return sorted(found)


def fetch(job):
    name,url,scope=job;folder=BASE/'regional_evidence';folder.mkdir(parents=True,exist_ok=True)
    path=folder/name
    record=dict(name=name,url=url,path=str(path.relative_to(ROOT)),scope=scope,retrieved_utc=datetime.now(timezone.utc).isoformat())
    try:
        if not path.exists():
            response=requests.get(url,timeout=(20,180));record['http_status']=response.status_code;response.raise_for_status()
            data=response.content
            if name.endswith('.pdf') and not data.startswith(b'%PDF'):raise ValueError('Response is not a PDF')
            if name.endswith('.xlsx') and not data.startswith(b'PK'):raise ValueError('Response is not an XLSX')
            path.write_bytes(data)
        data=path.read_bytes();record.update(status='downloaded',bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
    except (requests.RequestException,ValueError) as error:record.update(status='unavailable',error=str(error))
    return record


def main():
    BASE.mkdir(parents=True,exist_ok=True)
    page=BASE/'soni_outages.html'
    if not page.exists():
        response=requests.get('https://www.soni.ltd.uk/grid/outage-information',timeout=45);response.raise_for_status();page.write_text(response.text)
    jobs=[]
    for filename,path in documents(page_data(page)):
        if filename.lower().endswith(('.pdf','.xlsx')) and any(t in filename.lower() for t in ['outage','maintenance']):
            jobs.append((filename,urljoin('https://cms.soni.ltd.uk',path),'SONI published outage schedule/summary; inspect planned and actual status'))
    jobs += [
        ('SONI_all_island_TYTFS_2024.pdf','https://cms.soni.ltd.uk/sites/default/files/publications/All-Island-Ten-Year%20Transmission-Forecast-Statement-2024.pdf','All-island transmission topology/parameters and planning cases; copyrighted source reference'),
        ('NESO_2019_08_09_technical_report.pdf','https://www.neso.energy/document/152346/download','GB frequency/protection event validation'),
        ('NESO_2019_08_09_appendices.pdf','https://www.neso.energy/document/152351/download','GB disturbance sequence and observed response validation'),
        ('E3C_2019_08_09_final.pdf','https://assets.publishing.service.gov.uk/government/uploads/system/uploads/attachment_data/file/855767/e3c-gb-power-disruption-9-august-2019-final-report.pdf','GB demand-disconnection event validation'),
        ('E3C_Arwen_final_2022.pdf','https://assets.publishing.service.gov.uk/media/629fa8b1d3bf7f0371a9b0ca/storm-arwen-review-final-report.pdf','Official regional storm restoration validation'),
        ('SPEN_Arwen_reopener_2024.pdf','https://www.spenergynetworks.co.uk/userfiles/file/2024_01_31-SPEN-Storm-Arwen-Submission-Main-Document-Redacted.pdf','Southern Scotland/northern England storm damage and restoration validation; aggregate evidence'),
        ('NIE_resilience_2026.pdf','https://www.uregni.gov.uk/files/uregni/documents/2026-04/NIEN%20Resilience%20licence%20mods_0.pdf','NI storm damage, vegetation and restoration aggregate validation'),
        ('ENW_performance_2024_2025.pdf','https://www.enwl.co.uk/globalassets/stakeholder-engagement/responsible-business/documents/responsible-business-report-2024-2025.pdf','Northwest England performance and storm aggregate validation'),
        ('SPEN_performance_2024_2025.pdf','https://www.spenergynetworks.co.uk/userfiles/file/SP_Energy_Networks_Distribution_Annual_Performance_Report__2024-25.pdf','SPEN reliability and storm aggregate validation'),
    ]
    # Several SONI legacy paths mirror the same filename; avoid racing writes.
    jobs=list({name:(name,url,scope) for name,url,scope in jobs}.values())
    records=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i,record in enumerate(pool.map(fetch,jobs)):
            records.append(record)
            if i%20==0:print('Collected',i+1,'/',len(jobs),flush=True)
    (BASE/'regional_evidence_manifest.json').write_text(json.dumps(records,indent=2))
    print('Downloaded',sum(r['status']=='downloaded' for r in records),'of',len(records),'files',flush=True)


if __name__=='__main__':main()
