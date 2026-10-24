"""Write data/sample_storm.json: one synthetic GB storm the frontend replays when the backend is offline."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.simulation import Network
from backend.storm import run_storm

net=Network('gb')
result={**run_storm(net,seed=1),'network':net.payload()}
(ROOT/'data/sample_storm.json').write_text(json.dumps(result,separators=(',',':'))+'\n',encoding='utf-8')
final=result['frames'][-1]['status']
print('frames',len(result['frames']),'pylons down',final.count('pylon'),'tripped',final.count('overload'))
