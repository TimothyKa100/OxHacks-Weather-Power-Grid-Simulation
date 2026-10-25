import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from grid_common import node,edge
from grid_inventory import distinguish_site_records
from grid_cim import nged_locations
from grid_osm_graph import volts,capacity
class GridDataTests(unittest.TestCase):
 def test_missing_identifier_cannot_become_a_graph_link(self):
  with self.assertRaises(ValueError):edge(None,'line:1','source')
  with self.assertRaises(ValueError):edge('None','line:1','source')
 def test_reused_substation_name_does_not_cross_regions(self):
  locations=nged_locations()
  self.assertNotIn('clifton 33/11kv',locations)
  self.assertNotEqual(locations['EAST_MIDLANDS:clifton 33/11kv'],locations['SOUTH_WEST:clifton 33/11kv'])
 def test_shared_site_id_preserves_distinct_equipment_ratings(self):
  rows=[node('site:1','substation','NPG_inventory',source_asset_id='1',latitude=54.,longitude=-1.,capacity_mva=v) for v in [10.,20.]]
  nodes,links=distinguish_site_records(rows)
  self.assertEqual(len({n['node_id'] for n in nodes}),3);self.assertEqual(len(links),2)
  self.assertEqual({n['capacity_mva'] for n in nodes if n['node_type']=='substation_equipment_record'},{10.,20.})
  self.assertIsNone(nodes[0]['capacity_mva'])
 def test_tagged_voltage_and_generation_units(self):
  self.assertEqual(volts({'voltage':'11000;33000'}),[11.,33.])
  self.assertEqual(capacity({'plant:output:electricity':'2 GW'}),2000.)
  self.assertIsNone(capacity({}))
if __name__=='__main__':unittest.main()
