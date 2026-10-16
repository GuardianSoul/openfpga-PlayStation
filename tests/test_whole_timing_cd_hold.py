"""Strict preparation must not inherit a broad local-CD hold relaxation."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_pocket_whole_timing import strict_cd_hold
from tool_paths import ROOT


class StrictCDHold(unittest.TestCase):
    def test_known_broad_bound_only_gets_stronger(self):
        broad = (ROOT / 'tests/fixtures/cd_stream_held_min.tcl').read_text()
        safe = broad.replace('set_min_delay -13 ', 'set_min_delay 0 ')
        before = '# unrelated before\n'; after = '# unrelated after\n'
        self.assertEqual(strict_cd_hold(before + broad + after), (before + safe + after, 1))
        self.assertEqual(strict_cd_hold(before + safe + after), (before + safe + after, 0))
        for bad in (broad.replace('-13 ', '-20 '), broad.replace('req_lba', 'other_lba')):
            with self.assertRaisesRegex(ValueError, 'endpoint-scoped CDC review'):
                strict_cd_hold(bad)


if __name__ == '__main__':
    unittest.main()
