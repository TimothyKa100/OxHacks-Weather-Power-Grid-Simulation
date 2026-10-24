"""Export benchmark inputs from pandapower, with source metadata. Run from project root."""
import json
from pathlib import Path
import pandapower as pp
import pandapower.networks as pn

ROOT = Path(__file__).resolve().parents[1]
net = pn.case14()
pp.rundcpp(net, numba=False)
# Use the solved ppc: to_ppc initializes the external-grid Pg to zero even with
# init='results'. The solved ppc retains the actual balanced slack dispatch.
ppc = net._ppc
# Schematic, not surveyed coordinates. 20 branches include the five transformers.
positions = [(0.10,.17),(.28,.28),(.48,.14),(.51,.37),(.27,.48),(.28,.73),(.65,.37),(.80,.18),(.77,.55),(.92,.66),(.65,.76),(.13,.91),(.40,.94),(.76,.91)]
data = {
    "source": "pandapower.networks.case14 -> rundcpp(numba=False) -> solved net._ppc",
    "pandapower_version": pp.__version__, "base_mva": float(ppc["baseMVA"]),
    "buses": [{"id":i+1,"load_mw":float(b[2]),"x":positions[i][0],"y":positions[i][1]} for i,b in enumerate(ppc["bus"])],
    "generators": [{"bus":int(g[0])+1,"base_mw":float(g[1]),"max_mw":float(net.ext_grid.iloc[0].max_p_mw) if i==0 else (float(net.gen.iloc[i-1].max_p_mw) if g[1]>0 else 0.0)} for i,g in enumerate(ppc["gen"])],
    "branches": [{"id":f"L{i+1:02d}","from":int(b[0])+1,"to":int(b[1])+1,"x_pu":float(b[3]),"tap":float(b[8]) or 1.,"cost":1+i%3,"exposure":round(.75+(i*7%11)/20,3)} for i,b in enumerate(ppc["branch"])],
    "reference_flows_mw": ppc['branch'][:,13].real.tolist(),
    "notes": "Original nonzero active generators supply islands; zero-P synchronous condensers cannot generate active power. Costs, exposure, and derived thermal ratings are illustrative."
}
(ROOT/"data/ieee14.json").write_text(json.dumps(data,indent=2)+"\n")
print("Exported", len(data["buses"]), "buses and", len(data["branches"]), "branches")
