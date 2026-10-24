"""Convert route-mapped hourly CSV to dashboard JSON. Uses only Python stdlib.

CSV columns: timestamp,line_id,gust_kmh,rain_mm_h. One row per branch per hour.
Exposure mapping from geographic weather grids must happen before this conversion.
"""
import argparse
import csv
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.weather import validate_weather


def convert(path, source):
    benchmark=json.loads((Path(__file__).resolve().parents[1]/'data/ieee14.json').read_text())
    ids=[e['id'] for e in benchmark['branches']]
    grouped={}
    with open(path,newline='') as stream:
        for row in csv.DictReader(stream):
            stamp=row['timestamp']; asset=row['line_id']
            group=grouped.setdefault(stamp,{})
            if asset not in ids or asset in group:
                raise ValueError(f'Unknown or duplicate line {asset} at {stamp}')
            group[asset]=(float(row['gust_kmh']),float(row['rain_mm_h']))
    hours=[]
    for stamp, group in grouped.items():
        if set(group)!=set(ids): raise ValueError(f'Missing route exposures at {stamp}')
        hours.append(dict(timestamp=stamp,gust_kmh=[group[i][0] for i in ids],rain_mm_h=[group[i][1] for i in ids]))
    return validate_weather(dict(source=source,hours=hours),len(ids))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv'); parser.add_argument('output'); parser.add_argument('--source',required=True)
    args=parser.parse_args()
    try: result=convert(args.csv,args.source)
    except (ValueError,KeyError) as exc: parser.error(str(exc))
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
