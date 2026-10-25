"""Archive public operator evidence, with hashes, licences and honest access failures.

No credentials or third-party aggregator are required. Run periodically to accumulate
live snapshots; a snapshot is never presented as a retrospective fault archive.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/grid/raw/outages'
STAMP = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def download(name, url, suffix='json', scope='historical', licence=None):
    folder = OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    record = dict(source=name, url=url, retrieved_utc=STAMP, scope=scope, licence=licence)
    try:
        response = requests.get(url, timeout=(20, 180))
        record['http_status'] = response.status_code
        response.raise_for_status()
        if suffix == 'json': response.json()  # Never archive a login page as data.
        path = folder / f'{STAMP}.{suffix}'
        path.write_bytes(response.content)
        record.update(status='downloaded', path=str(path.relative_to(ROOT)), bytes=len(response.content),
                      sha256=hashlib.sha256(response.content).hexdigest())
    except (requests.RequestException, ValueError) as error:
        record.update(status='unavailable', error=str(error))
    print(name, record['status'], record.get('bytes', record.get('http_status')), flush=True)
    return record


def ods(host, dataset, scope):
    base = f'https://{host}/api/explore/v2.1/catalog/datasets/{dataset}'
    name = dataset.replace('@', '_')
    meta = download(name + '_metadata', base, scope='metadata')
    licence = None
    if meta['status'] == 'downloaded':
        metadata = json.loads((ROOT / meta['path']).read_text())
        licence = metadata.get('metas', {}).get('default', {}).get('license')
    data = download(name, base + '/exports/json?lang=en&timezone=UTC', scope=scope, licence=licence)
    if data['status']=='downloaded':
        count=len(json.loads((ROOT/data['path']).read_text()))
        data['records_downloaded']=count
        advertised=metadata.get('metas',{}).get('default',{}).get('records_count') if meta['status']=='downloaded' else None
        if count==0 and advertised:
            data.update(status='empty_export_with_advertised_records',advertised_records=advertised,
                        note='Anonymous export returned []; catalogue visibility does not grant row access. Registration may be required.')
    return [meta, data]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sources = [
        ('ukpowernetworks.opendatasoft.com', 'ukpn-ehv-network-outages', 'EHV outage schedule; planned and actual must be distinguished'),
        ('ukpowernetworks.opendatasoft.com', 'ukpn-live-faults', 'live snapshot'),
        ('northernpowergrid.opendatasoft.com', 'live-power-cuts-data', 'live snapshot; postcode rows may repeat incidents'),
        ('spenergynetworks.opendatasoft.com', 'all-hv-faults-spd-spm', 'historical unplanned HV faults'),
        ('spenergynetworks.opendatasoft.com', 'all-lv-faults-spd-spm', 'historical unplanned LV faults'),
        ('spenergynetworks.opendatasoft.com', 'all-ehv-faults-spd-spm', 'historical unplanned EHV faults'),
        ('electricitynorthwest.opendatasoft.com', 'unplanned-outages', 'historical unplanned faults; registration may be required'),
        ('electricitynorthwest.opendatasoft.com', 'sp-enw-live-neop', 'live snapshot'),
        ('nienetworks.opendatasoft.com', 'nie-networks-network-faults', 'live snapshot'),
        ('spenergynetworks.opendatasoft.com', 'distribution-network-live-outages', 'live snapshot'),
        ('spenergynetworks.opendatasoft.com', 'national-energy-outage-data', 'live snapshot'),
    ]
    records = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(lambda args: ods(*args), sources): records.extend(result)
    records.append(download('ssen_live','https://external.distribution.prd.ssen.co.uk/opendataportal-prd/v4/api/getallfaults',
                            scope='live snapshot',licence='CC BY 4.0'))
    records.append(download('nie_powercheck','https://powercheck.nienetworks.co.uk/NIEPowerCheckerWebAPI/api/faults',
                            scope='official public NIE customer-map snapshot; no historical coverage implied',
                            licence='Public customer map; redistribution terms not confirmed; retain source attribution'))
    # Discover all actual NGED resources instead of guessing private feed URLs.
    package_url = 'https://connecteddata.nationalgrid.co.uk/api/3/action/package_show?id=live-power-cuts'
    meta = download('nged_metadata', package_url, scope='metadata')
    records.append(meta)
    if meta['status'] == 'downloaded':
        package = json.loads((ROOT / meta['path']).read_text())['result']
        for resource in package['resources']:
            if resource['format'].upper() == 'CSV':
                records.append(download('nged_' + resource['id'], resource['url'], 'csv', 'live snapshot', package.get('rights')))
    path = OUT / f'manifest_{STAMP}.json'
    path.write_text(json.dumps(records, indent=2))
    (OUT / 'latest_manifest.json').write_text(json.dumps(records, indent=2))


def geocode_postcodes():
    """Cache public postcode centres in documented batches of at most 100."""
    import re
    import sys
    sys.path.insert(0,str(ROOT))
    from scripts.build_uk_outages import source_rows
    path=OUT/'postcode_coordinates.json'
    cache=json.loads(path.read_text()) if path.exists() else {}
    codes=sorted({p for row in source_rows() for p in json.loads(row['postcodes_json']) if p not in cache})
    def lookup(batch):
        response=requests.post('https://api.postcodes.io/postcodes',json={'postcodes':batch},timeout=45)
        response.raise_for_status()
        return {re.sub(r'\s+','',item['query']):item['result'] for item in response.json()['result']}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(lookup,[codes[i:i+100] for i in range(0,len(codes),100)]):
            cache.update(result);path.write_text(json.dumps(cache))
    write=dict(source='https://postcodes.io/docs/api/bulk-lookup-postcodes/',retrieved_utc=STAMP,
               postcode_units=len(cache),centroids_not_supply_areas=True,
               attribution='Contains OS, Royal Mail, National Statistics and NRS postcode data; see https://postcodes.io/about/')
    (OUT/'postcode_metadata.json').write_text(json.dumps(write,indent=2))


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--geocode-only',action='store_true');args=parser.parse_args()
    if args.geocode_only:geocode_postcodes()
    else:main()
