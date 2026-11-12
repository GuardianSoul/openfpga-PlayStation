from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_cue_hold_cells as cells
import prepare_save_core_reset as core_reset
from tool_paths import ROOT

MOUNT = (ROOT / 'rtl/pocket/psx_cue_mount.sv').read_text()


class CueHoldCells(unittest.TestCase):
    def test_cells_sit_only_between_store_data_and_the_lanes(self):
        out = cells.transform(MOUNT)
        self.assertEqual(cells.restore(out), MOUNT)
        self.assertEqual(out.count('psx_hold_cell hold_cell(.i(db_dat[cue_hold_bit]), .o(cue_hold_dat[cue_hold_bit]));'), 1)
        self.assertIn('cue_hold_bit < 32;', out)
        self.assertEqual(cells.CELLS, 32)
        for k in range(4):
            self.assertEqual(out.count(f'.a_d(cue_hold_dat[{8*k + 7}:{8*k}])'), 1)
        self.assertNotIn('.a_d(db_dat', out)
        # address, write enable and port B are untouched; no register is added
        for kept in ('.a_addr(a_addr),.a_we(a_we && db_sel[0])', '.b_addr(b_addr),.b_we(b_we),.b_d(bridge_data[7:0])'):
            self.assertIn(kept, out)
        count = lambda text, word: sum(line.split('//')[0].count(word) for line in text.splitlines())
        self.assertEqual(count(out, 'always @'), count(MOUNT, 'always @'))
        self.assertEqual(out.count('db_dat'), MOUNT.count('db_dat') - 4 + 1)

    def test_leaf_is_the_reviewed_identity_lut(self):
        leaf = (ROOT / 'rtl/pocket/psx_hold_cell.sv').read_text()
        self.assertEqual(cells.sha(leaf), cells.LEAF)
        self.assertIn(".lut_mask(64'hAAAAAAAAAAAAAAAA)", leaf)
        self.assertIn('.dataa(i), .combout(o)', leaf)

    def test_refuses_a_second_run_and_a_changed_ram(self):
        out = cells.transform(MOUNT)
        with self.assertRaises(ValueError):
            cells.transform(out)
        with self.assertRaises(ValueError):   # a fifth data port
            cells.transform(MOUNT.replace('.a_d(db_dat[7:0])', '.a_d(db_dat[7:0]),.a_d(db_dat[7:0])', 1))
        with self.assertRaises(ValueError):   # a lane wired differently
            cells.transform(MOUNT.replace('.a_d(db_dat[15:8])', '.a_d(db_dat[23:16])', 1).replace('.a_d(db_dat[23:16])', '.a_d(db_dat[15:8])', 1)
                            .replace('.a_d(db_dat[15:8])', '.a_d(db_dat[8:1])'))

    def test_faults_change_the_text_and_break_the_inverse(self):
        out = cells.transform(MOUNT)
        for name in cells.FAULTS:
            bad = cells.fault_text(out, name)
            self.assertNotEqual(bad, out)
            with self.assertRaises(ValueError):
                cells.restore(bad)


class SaveCoreResetFrozenForm(unittest.TestCase):
    def test_refuses_a_device_without_the_chain_steps(self):
        # the repository device has no SPU RAM port yet (prepare_save_pxs6 and later steps add it)
        with self.assertRaises(ValueError):
            core_reset.device_text((ROOT / 'rtl/pocket/psx_save_exp1.sv').read_text())

    def test_core_top_gets_the_core_reset(self):
        top = "        .clk2x(clk2x), .reset_2x(mc_reset2x[1]),\n"
        out = core_reset.core_top_text('    psx_save_exp1 save(\n' + top + '    );\n')
        self.assertIn('.core_reset(reset)', out)
        with self.assertRaises(ValueError):
            core_reset.core_top_text(out)


if __name__ == '__main__':
    unittest.main()
