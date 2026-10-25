"""Stage regional model inputs separately from observed physical-failure labels."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

import pandas as pd
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from failure_model.common import now,write_json
BASE=ROOT/'data/grid';RAW=BASE/'raw/research';OUT=BASE/'normalized'


def npg_references():
    manifest=json.loads((RAW/'outage_watch/npg_native_manifest.json').read_text())
    references={}
    for file in manifest:
        for ref,row in json.loads(Path(file['path']).read_text()).get('powercuts',{}).items():
            obj=row.get('networkObject')
            if obj is None:continue
            logged=pd.to_datetime(row.get('logged'),unit='ms',utc=True,errors='coerce')
            logged=logged.isoformat() if pd.notna(logged) else None
            key=(ref.upper(),str(obj),logged)
            entry=references.setdefault(key,dict(operator='NPG',incident_reference=ref.upper(),reported_network_object_id=str(obj),
                first_snapshot_commit_utc=file['collected_commit_time'],last_snapshot_commit_utc=file['collected_commit_time'],
                network_level=row.get('type'),region_as_published=row.get('region'),source_url=file['url'],
                reported_logged_utc=logged,
                identity_status='published_in_archived_DNO_feed_not_crosswalked_to_asset_inventory'))
            entry['first_snapshot_commit_utc']=min(entry['first_snapshot_commit_utc'],file['collected_commit_time'])
            entry['last_snapshot_commit_utc']=max(entry['last_snapshot_commit_utc'],file['collected_commit_time'])
    frame=pd.DataFrame(references.values());frame.to_parquet(OUT/'npg_reported_network_objects.parquet',index=False)
    return frame


def soni_plans():
    folder=RAW/'regional_evidence';rows=[]
    for path in sorted(folder.glob('Transmission*Plan*20*.xlsx')):
        book=pd.ExcelFile(path)
        for sheet in book.sheet_names:
            data=pd.read_excel(path,sheet_name=sheet).rename(columns=lambda x:str(x).strip())
            for index,row in data.iterrows():
                if 'SUB 1' not in row or pd.isna(row['SUB 1']):continue
                start=pd.to_datetime(row.get('Start'),errors='coerce');finish=pd.to_datetime(row.get('Finish'),errors='coerce')
                rows.append(dict(source_file=str(path.relative_to(ROOT)),sheet=sheet,source_row=int(index+2),
                    substation_1=str(row['SUB 1']),substation_2=str(row.get('SUB 2')) if pd.notna(row.get('SUB 2')) else None,
                    task_as_published=str(row.get('Task Name')),work_type=str(row.get('TYPE')),
                    voltage_as_published=str(row.get('kV')),duration_as_published=str(row.get('Duration')),
                    planned_start_local=start.isoformat() if pd.notna(start) else None,
                    planned_finish_local=finish.isoformat() if pd.notna(finish) else None,
                    date_order_valid=not(pd.notna(start) and pd.notna(finish) and finish<start),
                    record_kind='published_maintenance_plan',actual_occurrence_verified=False,physical_failure_verified=False))
    frame=pd.DataFrame(rows);frame.to_parquet(OUT/'soni_transmission_maintenance_plans.parquet',index=False)
    # Preserve the weekly text and reference spans, without inventing exact execution dates from Gantt shading.
    references=[];seen=set();doc_rows=[]
    pattern=re.compile(r'(?m)^\s*((?:PL)?\d{1,4}-20\d{2})\s*')
    for path in sorted(folder.glob('*.pdf')):
        if 'outage' not in path.name.lower() or 'summary' not in path.name.lower():continue
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:continue
        seen.add(digest)
        for page_no,page in enumerate(PdfReader(path).pages,1):
            text=page.extract_text() or '';doc_rows.append(dict(source_file=str(path.relative_to(ROOT)),sha256=digest,page=page_no,text=text))
            matches=list(pattern.finditer(text))
            for i,m in enumerate(matches):
                references.append(dict(source_file=str(path.relative_to(ROOT)),sha256=digest,page=page_no,
                    outage_plan_reference=m[1],description=text[m.end():matches[i+1].start() if i+1<len(matches) else len(text)].strip(),
                    record_kind='published_schedule_reference',actual_occurrence_verified=False,physical_failure_verified=False,
                    extraction_status='reference_text_span_requires_source_review'))
    docs=pd.DataFrame(doc_rows);docs.to_parquet(OUT/'soni_weekly_outage_documents.parquet',index=False)
    refs=pd.DataFrame(references);refs.to_parquet(OUT/'soni_weekly_outage_references.parquet',index=False)
    return frame,refs,len(seen)


def soni_generation_schedules():
    rows=[]
    for path in sorted((RAW/'regional_evidence').glob('Outage-Week-*.xlsx')):
        book=pd.ExcelFile(path)
        if 'Outage Dates' not in book.sheet_names:continue
        data=pd.read_excel(path,sheet_name='Outage Dates',header=None)
        headers=[i for i,row in data.head(15).iterrows() if 'Station' in row.values and 'Start Date' in row.values]
        if not headers:continue
        header=headers[0];data.columns=[str(c).strip() for c in data.iloc[header]]
        for index,row in data.iloc[header+1:].iterrows():
            if pd.isna(row.get('Station')) or pd.isna(row.get('Unit')):continue
            start=pd.to_datetime(row.get('Start Date'),errors='coerce');end=pd.to_datetime(row.get('End Date'),errors='coerce')
            station=str(row['Station']).strip()
            rows.append(dict(source_file=str(path.relative_to(ROOT)),source_row=int(index+1),station=station,unit=str(row['Unit']),
                outage_code_as_published=str(row.get('Value')),rating_as_published=str(row.get('NPR')),
                start_as_published=start.isoformat() if pd.notna(start) else None,
                end_as_published=end.isoformat() if pd.notna(end) else None,
                region='Northern Ireland' if any(name in station.upper() for name in ['BALLYLUMFORD','KILROOT','COOLKEERAGH']) else 'All-island station; region unresolved',
                record_kind='published_generation_outage_schedule',actual_failure_verified=False,
                end_time_status='schedule_or_availability_horizon_not_verified_restoration'))
    frame=pd.DataFrame(rows);frame.to_parquet(OUT/'soni_generation_outage_schedule_observations.parquet',index=False)
    return frame


def benchmark():
    folder=RAW/'pypsa_gb/data/network/reduced_network'
    buses=pd.read_csv(folder/'buses.csv');lines=pd.read_csv(folder/'lines.csv');links=pd.read_csv(folder/'links.csv')
    ids=set(buses.name)
    dangling=set(lines.bus0)|set(lines.bus1);dangling-=ids
    if dangling:raise ValueError('Research benchmark has missing line endpoints: '+str(dangling))
    for frame,name in [(buses,'buses'),(lines,'lines'),(links,'links')]:
        frame['source_kind']='PyPSA_GB_reduced_research_benchmark'
        frame['parameter_status']='as_published_reconcile_per_unit_base_and_PyPSA_units_before_use'
        frame.to_parquet(OUT/f'pypsa_gb_benchmark_{name}.parquet',index=False)
    return dict(buses=len(buses),lines=len(lines),links=len(links),dangling_line_endpoints=0,
                source_commit=json.loads((RAW/'pypsa_gb/manifest.json').read_text())['commit'],
                cascade_validated=False,scope='GB research benchmark, not NI or a verified present operational state')


def main():
    print('Recovering NPG network-object references',flush=True);npg=npg_references()
    print('Staging SONI plans and weekly reference documents',flush=True);plans,refs,documents=soni_plans()
    generation=soni_generation_schedules();model=benchmark()
    with sqlite3.connect(BASE/'uk_power_grid.sqlite') as db:
        npg.to_sql('npg_reported_network_objects',db,if_exists='replace',index=False)
        db.execute('CREATE INDEX npg_network_ref ON npg_reported_network_objects(incident_reference)')
        if 'reported_network_object_id' not in {r[1] for r in db.execute('PRAGMA table_info(outages)')}:
            db.execute('ALTER TABLE outages ADD COLUMN reported_network_object_id TEXT')
        # Only one consistent native object reference is exposed as a candidate ID.
        db.execute('''UPDATE outages SET reported_network_object_id=(SELECT MIN(r.reported_network_object_id)
            FROM npg_reported_network_objects r WHERE r.incident_reference=UPPER(outages.source_event_key)
            AND outages.archive_start_basis='reported'
            AND ABS(julianday(r.reported_logged_utc)-julianday(outages.reported_at_utc))<=1.0/24
            GROUP BY r.incident_reference
            HAVING COUNT(DISTINCT r.reported_network_object_id)=1)
            WHERE operator='NPG' AND source_kind='user_supplied_archive' ''')
        linked=db.execute("SELECT COUNT(*) FROM outages WHERE operator='NPG' AND reported_network_object_id IS NOT NULL").fetchone()[0]
        plans.to_sql('soni_maintenance_plans',db,if_exists='replace',index=False)
        refs.to_sql('soni_schedule_references',db,if_exists='replace',index=False)
        generation.to_sql('soni_generation_schedule_observations',db,if_exists='replace',index=False)
        db.commit()
        pd.read_sql_query('SELECT * FROM outages',db).to_parquet(OUT/'all_outage_reports.parquet',index=False)
    report=dict(generated_utc=now(),npg_native_network_reference_pairs=len(npg),
                npg_incident_references=int(npg.incident_reference.nunique()),npg_archive_reports_with_unique_native_object=linked,
                NIE_maintenance_plan_rows=len(plans),NIE_plan_date_errors=int((~plans.date_order_valid).sum()),
                SONI_weekly_documents=documents,SONI_weekly_reference_observations=len(refs),
                SONI_unique_plan_references=int(refs.outage_plan_reference.nunique()),GB_research_benchmark=model,
                all_island_generation_schedule_observations=len(generation),
                NI_named_generation_schedule_observations=int(generation.region.eq('Northern Ireland').sum()),
                strict_asset_failure_labels_added=0,
                note='Maintenance schedules and network-object pointers remain separate from confirmed failures and certified inventory identity.')
    write_json(BASE/'reports/regional_evidence.json',report);print(json.dumps(report,indent=2))


if __name__=='__main__':main()
