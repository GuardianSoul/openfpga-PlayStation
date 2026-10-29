import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_sdram_idle_paths as p
from prepare_sdram_ref_owed import ref_owed_text

CTRL = Path(__file__).resolve().parents[1] / 'rtl/memory/psx_sdram_ctrl.sv'


class SdramIdlePathsTest(unittest.TestCase):
    def test_rewrites_alone_and_on_ref_owed(self):
        for text in (CTRL.read_text(), ref_owed_text(CTRL.read_text())):
            out = p.idle_paths_text(text)
            self.assertIn('a <= idle_a;', out)
            self.assertNotIn('!took1 ? ch1_addr', out)
            with self.assertRaises(ValueError):
                p.idle_paths_text(out)

    def test_prepare_project_retargets_qsf(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'src').mkdir()
            (d / 'src/psx_sdram_ctrl.sv').write_text(CTRL.read_text())
            (d / 'apf_top.qsf').write_text(f'set_global_assignment -name SYSTEMVERILOG_FILE "{d}/src/psx_sdram_ctrl.sv"\n')
            out = p.prepare_project(d)
            self.assertIn(str(out), (d / 'apf_top.qsf').read_text())


if __name__ == '__main__':
    unittest.main()
