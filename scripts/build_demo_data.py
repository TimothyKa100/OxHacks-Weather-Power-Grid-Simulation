"""Copy what the simulation and demo need out of data/ into demo_data/ (backend/paths.py).

The full data/ tree (~18 GB of raw archives, training rows and weather caches) is only
needed to rebuild datasets or retrain. demo_data/ keeps the same layout, so the backend
reads either. Rerun after retraining or rebuilding networks/storms; manifest.json records
sizes and SHA-256 hashes.

Usage: python scripts/build_demo_data.py [--check]
"""
import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE, DEST = ROOT/'data', ROOT/'demo_data'
# (pattern relative to data/, what the run time uses it for)
FILES = [
    ('gb_network.json', 'GB backbone network (buses, branches, generators, routes)'),
    ('ieee14.json', 'IEEE 14-bus test network'),
    ('sample_weather.json', 'Legacy-mode weather profile'),
    ('sample_storm.json', 'Offline frontend fallback storm'),
    ('storms/*.json', 'Historical storm replays (gridded gusts and observed outages)'),
    ('grid/detail_graph.json', 'National 33-400 kV graph reduced into view networks'),
    ('grid/asset_index.parquet', 'Grid-archive assets for the map level-of-detail layer'),
    ('grid/ATTRIBUTION.md', 'Source licences for the grid-derived files'),
    ('failure_model/models/gb-corridor-xgb-v1/*', 'Corridor model v1 (comparison, archived exports)'),
    ('failure_model/models/gb-corridor-xgb-v2/*', 'Corridor model v2 (hybrid ML input)'),
    ('failure_model/exports/*_gb-corridor-xgb-v*.json', 'Archived complete-feature predictions for replays'),
    ('examples/*.json', 'Example uploads (paired hybrid profile)'),
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def selected():
    for pattern, purpose in FILES:
        matches = sorted(p for p in SOURCE.glob(pattern) if p.is_file())
        if not matches:
            raise FileNotFoundError(f'data/{pattern} is missing; build or extract it first')
        for path in matches:
            yield path.relative_to(SOURCE).as_posix(), purpose


def build():
    files = {}
    for rel, purpose in selected():
        target = DEST/rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE/rel, target)
        files[rel] = {'bytes': target.stat().st_size, 'sha256': sha256(target), 'purpose': purpose}
    stale = [p for p in DEST.rglob('*') if p.is_file() and p.relative_to(DEST).as_posix() not in files
             and p.name not in ('manifest.json', 'README.md') and 'view_networks' not in p.parts]
    for p in stale:
        p.unlink()
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    manifest = {'built_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'source_commit': commit,
                'total_bytes': sum(f['bytes'] for f in files.values()), 'files': files}
    (DEST/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest


def check():
    """Fail when demo_data/ differs from data/ (e.g. after retraining without rebuilding)."""
    manifest = json.loads((DEST/'manifest.json').read_text())
    problems = [rel for rel, _ in selected() if rel not in manifest['files'] or sha256(SOURCE/rel) != manifest['files'][rel]['sha256']]
    problems += [rel for rel, f in manifest['files'].items() if not (DEST/rel).exists() or sha256(DEST/rel) != f['sha256']]
    return sorted(set(problems))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--check', action='store_true', help='Verify demo_data/ against data/ without copying')
    if parser.parse_args().check:
        problems = check()
        print(json.dumps({'up_to_date': not problems, 'differs': problems}, indent=2))
        raise SystemExit(1 if problems else 0)
    m = build()
    print(json.dumps({'files': len(m['files']), 'megabytes': round(m['total_bytes']/1e6, 1), 'destination': str(DEST)}, indent=2))
