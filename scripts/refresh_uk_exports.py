"""Refresh canonical point/weather exports after Atlas imports and geographic pruning."""
import json
from pathlib import Path
import sqlite3
import sys

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.prepare_grid_conditions import build_node_weather,export_nodes,sampling_plan
from failure_model.common import write_json,now


def main():
    base=ROOT/'data/grid'
    with sqlite3.connect(base/'uk_power_grid.sqlite') as db:
        print('Refreshing node weather links',flush=True);weather=build_node_weather(db)
        print('Exporting retained nodes and hierarchy',flush=True);export_nodes(db)
        plan=sampling_plan(db)
        old=json.loads(db.execute("SELECT value FROM metadata WHERE key='node_conditions'").fetchone()[0])
        totals=dict(db.execute('SELECT operator,COUNT(*) FROM outages GROUP BY operator'))
        count=sum(totals.values())
        old.update(generated_utc=now(),node_weather=weather,national_node_sampling_plan=plan,
                   outages={'total':count,'mapped':count,'unmapped':0,'by_operator':totals,
                            'verified_asset_failures':0,'training_eligible':0,
                            'scope':'Retained UK land historical incidents plus observed live snapshots; uneven coverage'})
        db.execute("UPDATE metadata SET value=? WHERE key='node_conditions'",(json.dumps(old),))
        pd.read_sql_query('SELECT * FROM node_flood_history',db).to_parquet(base/'normalized/node_recorded_flood_history.parquet',index=False)
        # If a feeder was removed, its old proxy flood join cannot be retained.
        db.execute('UPDATE outages SET recorded_flood_outline_matches=NULL WHERE location_asset_id IS NULL')
        db.commit()
        pd.read_sql_query('SELECT * FROM outages',db).to_parquet(base/'normalized/all_outage_reports.parquet',index=False)
        cleaning=json.loads(db.execute("SELECT value FROM metadata WHERE key='cleaning'").fetchone()[0])
        counts={table:db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in ['nodes','edges','relationships','candidate_edges']}
        refinement=json.loads(db.execute("SELECT value FROM metadata WHERE key='uk_refinement'").fetchone()[0])
        refinement['counts']=counts
        db.execute("UPDATE metadata SET value=? WHERE key='uk_refinement'",(json.dumps(refinement),));db.commit()
    write_json(base/'reports/cleaning.json',cleaning)
    write_json(base/'reports/node_conditions.json',old)
    write_json(base/'reports/uk_refinement.json',refinement)
    print(json.dumps({'weather':weather,'outages':totals,'sampling_plan':plan},indent=2))


if __name__=='__main__':main()
