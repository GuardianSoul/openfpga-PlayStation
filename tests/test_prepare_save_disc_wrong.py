"""prepare_save_disc_wrong.py: the core_top transform."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_save_disc_wrong as step

CT = 'a\n' + step.OLD + ''.join(o + '\n' for o, _ in step.CT2) + 'b\n'
CM = ''.join(o for o, _ in step.CM)


class CoreTop(unittest.TestCase):
    def test_transform_and_inverse(self):
        new = step.ct_text(CT)
        self.assertEqual(step.restore(new), CT)
        self.assertIn('save_disc_wrong_sync[0],cue_mount_failed};', new)
        self.assertNotIn('&& cue_mounted', new)
        with self.assertRaises(ValueError):
            step.ct_text(new)

    def test_disc_mux_and_latch(self):
        new, cm = step.ct_text(CT), step.cm_text(CM)
        self.assertIn('.selected_disc(save_menu_disc)', new)
        self.assertIn("save_cue_disc_sync1[3:0]==4'd0 ? 3'd0 : save_cue_disc_sync1[2:0]-3'd1", new)
        self.assertIn('.cur_disc_o(cue_cur_disc)', new)
        self.assertIn('if(db_dat[4]) cur_disc_o<=db_dat[3:0];', cm)
        self.assertEqual(step.restore(cm), CM)
        self.assertIn('save_cue_disc_sync0[*]', step.sdc_text(''))

    def test_faults_apply(self):
        new, cm = step.ct_text(CT), step.cm_text(CM)
        for name, (old, _) in step.FAULTS.items():
            src = cm if 'cur_disc_o<=' in old else new
            self.assertNotEqual(step.fault_text(src, name), src, name)


if __name__ == '__main__':
    unittest.main()
