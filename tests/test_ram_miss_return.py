import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_ram_miss_return as p
from prepare_sdram_force_reg import force_reg_text
from prepare_sdram_idle_paths import idle_paths_text
from prepare_sdram_ref_owed import ref_owed_text

CTRL = Path(__file__).resolve().parents[1] / 'rtl/memory/psx_sdram_ctrl.sv'


class RamMissReturnTest(unittest.TestCase):
    def release_like(self):
        return force_reg_text(idle_paths_text(ref_owed_text(CTRL.read_text())))

    def test_edits_once_and_refuses_twice(self):
        out = p.controller_text(self.release_like())
        self.assertNotIn('x_ch1_dout32', out)
        self.assertIn('assign ch1_ready = miss_read_return ? x_ch1_ready_cpu : ch1_ready_q;', out)
        self.assertEqual(out.count('x_ch1_ready_cpu <= p_ch1 || ev_ch1_ready || start_ch1w;'), 1)
        self.assertIn('ch1_ready_q <= x_ch1_ready && !miss_read_return;', out)
        self.assertEqual(out.count('miss_read_return <= 1;'), 1)
        self.assertEqual(out.count('miss_read_return <= 0;'), 2)
        # the publish block reads ev_ch1_ready, so it must follow its declaration
        self.assertLess(out.index('wire ev_ch1_ready'), out.index('ch1_publish_word <= ch1_word;'))
        with self.assertRaises(ValueError):
            p.controller_text(out)


if __name__ == '__main__':
    unittest.main()
