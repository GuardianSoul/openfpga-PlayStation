"""Strict preparation must not inherit a broad local-CD hold relaxation."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_pocket_whole_timing import strict_cd_hold
from prepare_pocket_cd_cdc import MENU_MINS, legacy_held_lists
from tool_paths import ROOT


class StrictCDHold(unittest.TestCase):
    def test_table_and_sbi_lists_only_strengthen_the_known_bound(self):
        for held in legacy_held_lists(disc_menu=True,table_offset=True,libcrypt=True):
            broad = f'set_min_delay -13 -from [get_registers {{{held}}}]\n'
            safe = broad.replace('set_min_delay -13 ', 'set_min_delay 0 ')
            self.assertEqual(strict_cd_hold(broad,disc_menu=True),(safe,1))
            self.assertEqual(strict_cd_hold(safe,disc_menu=True),(safe,0))
            for bad in (broad.replace('m_tb','unknown'),broad.replace('m_lc_bytes','unknown')):
                if bad != broad:
                    with self.assertRaises(ValueError):strict_cd_hold(bad,disc_menu=True)

    def test_menu_bound_requires_explicit_profile_and_only_gets_stronger(self):
        self.assertEqual(strict_cd_hold(MENU_MINS[1], disc_menu=True), (MENU_MINS[0], 1))
        self.assertEqual(strict_cd_hold(MENU_MINS[0], disc_menu=True), (MENU_MINS[0], 0))
        for line, menu in ((MENU_MINS[1], False),
                           (MENU_MINS[1].replace('m_present', 'other_present'), True),
                           (MENU_MINS[1].replace('-13 ', '-20 '), True)):
            with self.subTest(line=line, menu=menu), self.assertRaises(ValueError):
                strict_cd_hold(line, disc_menu=menu)

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
