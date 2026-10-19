# SPDX-License-Identifier: MIT
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_video_368_budget import video_368_budget_text
from run_video_368_budget import prove_encoding


class Video368Budget(unittest.TestCase):
    def test_exhaustive_state_relation_and_centering(self):
        result = prove_encoding()
        self.assertTrue(result['passed'])
        self.assertEqual(result['tracker_transitions'], 32768)
        self.assertEqual(result['centering_combinations'], 22528)

    def test_unreviewed_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unreviewed 368-slot source'):
            video_368_budget_text('module psx_apf_video_wide; endmodule\n')


if __name__ == '__main__':
    unittest.main()
