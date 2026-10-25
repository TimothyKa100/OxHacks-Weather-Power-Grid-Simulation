"""Where the backend reads its run-time data.

demo_data/ holds only what the simulation and demonstration need (networks, storm replays,
the detail graph and asset index, the saved corridor models and their archived storm
exports); scripts/build_demo_data.py copies it out of the full data/ tree and records
checksums. The full data/ tree (~18 GB) is only needed to rebuild datasets or retrain.

GRIDGUARD_DATA overrides the choice; otherwise demo_data/ is used when it has been built.
Both folders share one layout, so paths below are the same relative to either.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT/'demo_data'
FULL = ROOT/'data'


def data_root():
    override = os.environ.get('GRIDGUARD_DATA')
    if override:
        return Path(override).resolve()
    return DEMO if (DEMO/'manifest.json').exists() else FULL


DATA = data_root()
