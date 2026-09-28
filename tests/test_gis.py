import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('gis', Path(__file__).resolve().parents[1] / 'backend' / 'gis.py')
gis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gis)


class GISTest(unittest.TestCase):
    def test_missing_is_not_zero(self):
        result=gis.compute_gis([{'id':'a','tokens':[]}], [], '파크', 'park')
        self.assertEqual(result['status'], 'not_ready')
        self.assertIsNone(result['summary']['target_median_m'])
        self.assertEqual(result['coverage']['measured_count'],0)

    def test_matched_comparison_and_determinism(self):
        records=[{'id':str(i),'sido':'예시','sigungu':'예시구','approval_year':2010,'tokens':[{'canonical':'파크'}] if i<6 else []} for i in range(12)]
        distances=[{'id':str(i),'feature':'park','distance_m':i*100} for i in range(12)]
        a=gis.compute_gis(records,distances)
        self.assertEqual(a,gis.compute_gis(records,distances))
        self.assertEqual(a['summary']['target_median_m'],250)
        self.assertEqual(a['summary']['control_median_m'],850)
        self.assertEqual(a['summary']['matched_strata'],1)
        self.assertLess(a['summary']['permutation_p_value'],0.05)

    def test_no_mixed_stratum_test(self):
        records=[{'id':str(i),'sido':'예시','sigungu':'A' if i<5 else 'B','approval_year':2000,'tokens':[{'canonical':'파크'}] if i<5 else []} for i in range(10)]
        distances=[{'id':str(i),'feature':'park','distance_m':i*100} for i in range(10)]
        result=gis.compute_gis(records,distances)
        self.assertIsNone(result['summary']['permutation_p_value'])
        self.assertEqual(result['summary']['matched_target_n'],0)

    def test_invalid_threshold(self):
        with self.assertRaises(ValueError):
            gis.compute_gis([],[],threshold_m=float('nan'))

if __name__=='__main__':
    unittest.main()
