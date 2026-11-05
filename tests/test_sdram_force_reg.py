import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_sdram_force_reg as p
from prepare_sdram_idle_paths import idle_paths_text
from prepare_sdram_ref_owed import ref_owed_text

CTRL = Path(__file__).resolve().parents[1] / 'rtl/memory/psx_sdram_ctrl.sv'


class SdramForceRegTest(unittest.TestCase):
    def test_registers_refresh_force_alone_and_stacked(self):
        base = CTRL.read_text()
        for text in (base, idle_paths_text(ref_owed_text(base))):
            out = p.force_reg_text(text)
            self.assertIn('wire force_rise = force_in && !force_d;', out)
            self.assertIn('force_in <= refreshForce;', out)
            with self.assertRaises(ValueError):
                p.force_reg_text(out)


if __name__ == '__main__':
    unittest.main()
