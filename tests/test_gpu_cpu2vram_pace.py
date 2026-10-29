import shutil, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import prepare_gpu_cpu2vram_pace as p

SRC = ROOT / 'references/PSX_MiSTer/rtl/gpu_cpu2vram.vhd'


class GpuCpu2VramPaceTest(unittest.TestCase):
    def test_transform(self):
        out = p.transform(SRC.read_text(), every=5)
        self.assertEqual(p.transform(out), out)
        self.assertIn("pixelStall = '0' and paceHold = '0'", out)
        self.assertIn('integer range 0 to 4', out)
        self.assertIn('if (paceCount = 4) then', out)

    def test_project_copy_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve(); shutil.copy(SRC, root / 'gpu_cpu2vram.vhd')
            (root / 'apf_top.qsf').write_text(f'set_global_assignment -name VHDL_FILE "{root}/gpu_cpu2vram.vhd"\n')
            p.apply(root)
            self.assertIn(p.TAG, (root / 'gpu_cpu2vram.vhd').read_text())
            (root / 'apf_top.qsf').write_text(f'set_global_assignment -name VHDL_FILE "{SRC}"\n')
            with self.assertRaises(SystemExit):
                p.apply(root)

    def test_rejects_unknown_source(self):
        with self.assertRaises(ValueError):
            p.transform('no gpu here\n')


if __name__ == '__main__':
    unittest.main()
