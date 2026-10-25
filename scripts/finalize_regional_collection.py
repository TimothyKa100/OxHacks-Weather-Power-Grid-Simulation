"""Publish evidence inventories and explicit model-readiness requirements."""
import json
from pathlib import Path
import sqlite3
import sys

import pandas as pd
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.import_outage_archive import assign_country
from failure_model.common import now,write_json
BASE=ROOT/'data/grid';OUT=BASE/'normalized'


def ni_benchmarks():
    source=BASE/'raw/research/regional_evidence/NIE_resilience_2026.pdf'
    text=PdfReader(source).pages[13].extract_text() or ''
    if not all(token in text for token in ['2,687','325,361','335','1,511']):
        raise ValueError('NIE source table changed: recheck Table 2.2 before extracting targets')
    url='https://www.uregni.gov.uk/files/uregni/documents/2026-04/NIEN%20Resilience%20licence%20mods_0.pdf'
    groups=['33kV','11kV overhead mainline','11kV overhead spur','6.6kV','LV','Total']
    counts=[71,480,616,9,1511,2687];interruptions=[33371,240112,36316,6095,9467,325361];cml=[14,335,103,4,57,514]
    table=pd.DataFrame(dict(event='Storm Eowyn 2025-01-24',operator='NIE',network_class=groups,
                            reported_faults=counts,customer_interruptions=interruptions,customer_minutes_lost_millions=cml))
    table['source_url']=url;table['source_page']=14;table['source_table']='2.2'
    table['use']='Aggregate storm-impact validation; table population differs from narrative over-5000 faults; rounded CML'
    table.to_csv(OUT/'NI_Eowyn_2025_validation_targets.csv',index=False)
    lengths=pd.DataFrame(dict(network_class=['Transmission','33kV','11kV','LV','Total'],
                             total_km=[2274,4259,25412,29557,61502],underground_km=[117,1286,4837,22507,28747],
                             overhead_km=[2157,2973,20575,7050,32755]))
    lengths['source_url']=url;lengths['source_page']=13;lengths['source_table']='2.1'
    lengths['use']='Published current aggregate network lengths; not historical per-asset at-risk exposure'
    lengths.to_csv(OUT/'NI_network_lengths_validation.csv',index=False)
    assert table.reported_faults.iloc[:-1].sum()==table.reported_faults.iloc[-1]
    assert table.customer_interruptions.iloc[:-1].sum()==table.customer_interruptions.iloc[-1]
    return len(table),len(lengths)


def main():
    targets,lengths=ni_benchmarks()
    with sqlite3.connect(BASE/'uk_power_grid.sqlite') as db:
        missing=pd.read_sql_query('SELECT event_key,operator,longitude,latitude FROM outages WHERE country IS NULL',db)
        if not missing.empty:
            rows=missing.to_dict('records');assign_country(rows)
            db.executemany('UPDATE outages SET country=?,geographic_region=? WHERE event_key=?',
                           [(r['country'],r['geographic_region'],r['event_key']) for r in rows])
        db.execute('CREATE INDEX IF NOT EXISTS outage_region_time ON outages(geographic_region,reported_at_local)')
        db.commit()
        observations=pd.read_sql_query('''SELECT event_key,source_event_key,operator,geographic_region,country,
            reported_at_utc,reported_at_local,archive_start_basis,restored_at_utc,archive_end_basis,
            latitude,longitude,location_basis,location_asset_id,reported_network_object_id,
            cause,customers_affected,planned_status,source_kind,training_eligible,
            asset_failure_verified,reporting_coverage_verified FROM outages
            WHERE geographic_region IN ('Southern Scotland','Northern England','Northern Ireland')''',db)
        observations['calibration_use']='Observed interruption evidence; event time, identity and exposure require their own verification'
        observations.to_parquet(OUT/'priority_region_outage_observations.parquet',index=False)
        counts=dict(db.execute('SELECT operator,COUNT(*) FROM outages GROUP BY operator'))
        total=sum(counts.values())
        for key in ['outages','node_conditions']:
            previous=db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
            if not previous:continue
            meta=json.loads(previous[0]);overview=dict(total=total,mapped=total,unmapped=0,by_operator=counts,
                verified_asset_failures=0,training_eligible=0,scope='Historical reports, supplied archives and official live snapshots; observation bases retained')
            if key=='outages':meta=overview
            else:meta['outages']=overview
            db.execute('UPDATE metadata SET value=? WHERE key=?',(json.dumps(meta),key))
        db.commit()
        pd.read_sql_query('SELECT * FROM outages',db).to_parquet(OUT/'all_outage_reports.parquet',index=False)
    acquired=json.loads((BASE/'reports/regional_evidence.json').read_text())
    requirements=[
        dict(requirement='Historical unplanned incidents with stable failed circuit/asset IDs',status='partial',
             available='SSEN NaFIRS proxies; supplied area archives; 1622 NPG native network-object matches',
             missing='SPEN HV/LV full records and certified circuit-code crosswalk; NPG DMS-object crosswalk; NIE historical fault log'),
        dict(requirement='Verified operating exposure and state/repair history',status='missing',
             available='Archive first/last sightings and some reported restoration; planned maintenance schedules',
             missing='Asset in-service dates, complete reporting intervals, actual switching/outage/restoration stages'),
        dict(requirement='Weather and asset vulnerability covariates',status='partial',
             available='ERA5 subsets; observed 2021/2025 station-years; terrain, mapped woodland, assumed tower heights',
             missing='Full hazard/asset-date coverage, asset age/condition, vegetation clearance, icing/lightning/flood depth'),
        dict(requirement='Connected electrical topology and physical parameters',status='partial',
             available='Raw CIM/LTDS/ETYS; pinned GB research benchmarks; SONI all-island topology/parameter reference',
             missing='Verified present/historical bus-breaker connectivity, NI machine-readable case and identity crosswalk'),
        dict(requirement='Demand, dispatch and operating cases',status='partial',
             available='Generation inventory/REMIT, demand allocation benchmark, SONI generator schedules and forecast dispatch tables',
             missing='Time-aligned bus P/Q demand, generator P/Q/limits and actual outage/switch/tap states for validation events'),
        dict(requirement='Protection, controls and dynamic models',status='missing',
             available='2019 technical disturbance reports and NI maintenance/protection-test descriptions',
             missing='Relay pickups and delays, breaker timing, UFLS/RoCoF logic, generator governors/exciters and inertia/control parameters'),
        dict(requirement='Independent validation events',status='partial',
             available='2019 GB event reports; NI Eowyn class-level faults/CI/CML; Arwen review; regional incident observations',
             missing='High-resolution fault/relay sequence, power/frequency/voltage traces and matched pre-event operating case'),
    ]
    report=dict(generated_utc=now(),active_outage_reports=total,active_by_operator=counts,
        priority_region_observations=observations.groupby('geographic_region').size().to_dict(),
        NI_aggregate_target_rows=targets,NI_network_length_rows=lengths,
        calibrated_asset_failure_model_ready=False,validated_nationwide_cascade_ready=False,
        requirements=requirements,acquired_evidence=acquired,
        topology_scope='GB and the synchronous all-island system require separate models coupled via HVDC; retain ROI network context or an equivalent for NI',
        catalogue_scope='Coordinate/land filtering belongs to the map catalogue. Raw electrical topology includes necessary unlocated buses and is a separate simulation input.')
    write_json(BASE/'reports/model_data_readiness.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ['requirements','acquired_evidence']},indent=2))


if __name__=='__main__':main()
