"""port-constraints step (scripts/prepare_port_constraints.py)."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_port_constraints as step
try:
    import prepare_hold_fit_target as hold   # optional: only on the hold-constraints branch
except ImportError:
    hold = None

SDC = 'derive_clock_uncertainty\n' + step.ANCHOR + ' ...\n'


class PortConstraints(unittest.TestCase):
    def test_once_before_anchor_and_composes_with_hold_step(self):
        out = step.sdc_text(SDC)
        self.assertEqual(out.count(step.MARKER + '_END'), 1)
        self.assertLess(out.index(step.MARKER), out.index(step.ANCHOR))
        if hold:
            self.assertIn(hold.MARKER, hold.sdc_text(out))
        with self.assertRaises(ValueError):
            step.sdc_text(out)
        with self.assertRaises(ValueError):
            step.sdc_text('x\n')

    def test_ddr_both_edges_and_center_aligned_exceptions(self):
        text = '\n'.join(step.LINES)
        for edge in ('-max 0 ', '-min 0 ', '-clock_fall -max 0 -add_delay', '-clock_fall -min 0 -add_delay'):
            self.assertIn(f'set_output_delay -clock scal_clk_out {edge}', text)
        for check, a, b in (('setup', 'rise', 'fall'), ('setup', 'fall', 'rise'), ('hold', 'rise', 'rise'), ('hold', 'fall', 'fall')):
            self.assertIn(f'set_false_path -{check} -{a}_from', text)
            self.assertIn(f'-{b}_to [get_clocks scal_clk_out]', text)
        self.assertEqual(text.count('set_false_path'), 4)          # nothing else is cut
        self.assertEqual(text.count('set_multicycle_path'), 2)    # audio window only
        self.assertIn('-setup -end 2', text)
        self.assertIn('-hold -end 2', text)
        self.assertNotIn('dram_', text)
        self.assertNotIn('bridge_', text)


if __name__ == '__main__':
    unittest.main()
