"""The pin harness must reject source drift and false/partial pass verdicts."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from run_apf_bridge_checks import BRIDGE, COMMON, COMMON_SHA256, digest, fault_profiles, profiles, simulation_copy, verdict


class APFBridgeChecks(unittest.TestCase):
    def test_only_mechanical_simulation_copy(self):
        original = BRIDGE.read_text()
        text = simulation_copy(original)
        self.assertIn('always @(posedge phy_spiclk or posedge phy_spiss)', text)
        self.assertIn('rx_dat[7:6] <= {phy_spimosi, phy_spimiso}', text)
        self.assertIn('assign phy_spiclk = phy_spiclk_driver;', text)
        self.assertIn('synch_3 s00(reset_n, reset_n_s, clk, , );', text)
        self.assertEqual(text.count('phy_spiclk_driver <='), 5)
        self.assertEqual(BRIDGE.read_text(), original)
        self.assertEqual(digest(COMMON), COMMON_SHA256)

    def test_unknown_source_or_clock_is_rejected(self):
        original = BRIDGE.read_text()
        for old, new in [('posedge phy_spiclk', 'negedge phy_spiclk'),
                         ('spis_word[31]', 'spis_word[30]'),
                         ('rx_byte_done_r_2', 'new_flag')]:
            with self.assertRaises(ValueError):simulation_copy(original.replace(old, new))

    def test_only_complete_single_verdict_passes(self):
        good = 'PASS APF_BRIDGE writes=16 reads=16 aborts=2 rx_edges=800 tx_pairs=256\n'
        self.assertTrue(verdict(good, 0))
        for output, code in [(good, 1), (good+good, 0), ('PASS APF_BRIDGE\n', 0),
                             (good.replace('reads=16', 'reads=15'), 0),
                             (good.replace('rx_edges=800', 'rx_edges=799'), 0),
                             (good+'FAIL\n', 0), (good+'FATAL\n', 0), ('', 0)]:
            self.assertFalse(verdict(output, code), (output, code))
        self.assertTrue(verdict('FATAL BRIDGE_READ got=0', 1, 'BRIDGE_READ got='))
        self.assertFalse(verdict('compiler failed', 1, 'BRIDGE_READ got='))
        self.assertFalse(verdict('BRIDGE_READ got=0', 0, 'BRIDGE_READ got='))
        self.assertFalse(verdict(good+'BRIDGE_READ got=0', 1, 'BRIDGE_READ got='))

    def test_profile_scope_contains_skews_and_both_oracle_faults(self):
        positive = profiles()
        self.assertEqual(len(positive), 25)
        self.assertEqual(len({p['name'] for p in positive}), len(positive))
        self.assertEqual(len([p for p in positive if p['name'].startswith('phase-')]), 16)
        self.assertTrue(all(p['parameters'].get('CHECK_WINDOWS', 1) == 1 for p in positive))
        self.assertEqual(len(fault_profiles()), 6)
        self.assertEqual(len([p for p in fault_profiles() if p.get('mutation')]), 2)


if __name__ == '__main__':unittest.main()
