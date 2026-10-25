"""Latest REMIT message states and candidate Atlas BMU-to-station crosswalk.

Unit identity is published by Elexon; station identity uses the Atlas crosswalk
(which includes fuzzy/manual matching). Only on-land UK mapped rows are served.
Raw messages retain revisions and units with no coordinate for auditing.
"""
import json
from pathlib import Path
import sqlite3
import sys

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from failure_model.common import now,write_json
BASE=ROOT/'data/grid'


def main():
    raw=BASE/'raw/outages/elexon_remit';latest={};revision_count=0
    for path in sorted(raw.glob('details_*.json')):
        for message in json.loads(path.read_text()).get('data',[]):
            revision_count+=1
            ident=message['mrid'];old=latest.get(ident)
            if old is None or (message.get('revisionNumber',0),message.get('publishTime',''))>(old.get('revisionNumber',0),old.get('publishTime','')):
                latest[ident]=message
    mapping=json.loads((BASE/'raw/uk_grid_atlas/src/data/gb/bmu-map.json').read_text())['byUnit']
    rows=[];excluded=[]
    with sqlite3.connect(BASE/'uk_power_grid.sqlite') as db:
        for message in latest.values():
            unit=message.get('assetId');station=mapping.get(unit);node=None
            if station:
                node=db.execute('''SELECT node_id,longitude,latitude FROM nodes WHERE node_id=? OR node_id=? LIMIT 1''',
                                ('OSM:'+station.replace('/',':'),f'UK_GRID_ATLAS:gb:stations:{station}')).fetchone()
            entry=dict(message_id=message.get('id'),mrid=message['mrid'],revision=message.get('revisionNumber'),
                       unit_id=unit,unit_eic=message.get('affectedUnitEIC'),affected_unit=message.get('affectedUnit'),
                       event_type=message.get('eventType'),unavailability_type=message.get('unavailabilityType'),
                       event_status=message.get('eventStatus'),start_utc=message.get('eventStartTime'),
                       end_utc_as_published=message.get('eventEndTime'),published_utc=message.get('publishTime'),
                       cause=message.get('cause'),fuel=message.get('fuelType'),
                       normal_capacity_mw=message.get('normalCapacity'),unavailable_capacity_mw=message.get('unavailableCapacity'),
                       available_capacity_mw=message.get('availableCapacity'),
                       station_node_id=node[0] if node else None,longitude=node[1] if node else None,latitude=node[2] if node else None,
                       link_basis='Atlas_BMU_station_crosswalk_candidate' if node else 'no_UK_land_station_crosswalk',
                       physical_failure_verified=False,customer_interruption_verified=False,
                       source_url=f'https://data.elexon.co.uk/bmrs/api/v1/remit/{message.get("id")}',
                       properties_json=json.dumps(message))
            (rows if node else excluded).append(entry)
        mapped=pd.DataFrame(rows);mapped.to_sql('generation_outages',db,if_exists='replace',index=False)
        db.execute('CREATE INDEX generation_unit_idx ON generation_outages(unit_id)')
        db.execute('CREATE INDEX generation_station_idx ON generation_outages(station_node_id)')
        db.commit()
    mapped.to_parquet(BASE/'normalized/generation_unavailability.parquet',index=False)
    pd.DataFrame(excluded).to_parquet(BASE/'normalized/excluded_unlocated_generation_reports.parquet',index=False)
    report=dict(generated_utc=now(),downloaded_revisions=revision_count,latest_messages=len(latest),
                UK_land_mapped_messages=len(rows),unlocated_or_outside_UK_land=len(excluded),
                mapped_unit_ids=int(mapped.unit_id.nunique()),event_statuses=mapped.event_status.value_counts().to_dict(),
                unavailability_types=mapped.unavailability_type.value_counts().to_dict(),
                interpretation='Unit unavailability, not customer power cuts. Latest revision per MRID; related MRIDs can still describe the same underlying event. End times may be forecasts. Atlas station matches are candidates, not verified failed components.')
    write_json(BASE/'reports/generation_unavailability.json',report);print(json.dumps(report,indent=2))


if __name__=='__main__':main()
