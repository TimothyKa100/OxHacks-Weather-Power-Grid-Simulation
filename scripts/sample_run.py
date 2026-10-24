"""Reproduce the default experiment without the browser."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.simulation import Network, run_experiment

result=run_experiment(Network(),intensity=100,seed=26,samples=128,budget=5)
(ROOT/'data/sample_run.json').write_text(json.dumps(result,indent=2)+'\n')
for key,portfolio in result['portfolios'].items():
    print(key,portfolio['selected'],portfolio['metrics']['mean_unserved_mwh'],'MWh')
print('Compute:',result['elapsed_seconds'],'seconds')
