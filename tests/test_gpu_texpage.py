import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_gpu_texpage as p

GPU = 'a\n' + ''.join(old for _, old, _ in p.EDITS) + 'b\n'


class GpuTexpageTest(unittest.TestCase):
    def test_transform(self):
        out = p.transform(GPU)
        self.assertEqual(p.transform(out), out)
        self.assertIn('texDisableAllow <= bus_dataWrite(0);', out)
        self.assertEqual(out.count('and texDisableAllow'), 4)

    def test_project(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'gpu.vhd').write_text(GPU)
            (root / 'apf_top.qsf').write_text(f'set_global_assignment -name VHDL_FILE "{root}/gpu.vhd" -library work\n')
            p.apply(root)
            self.assertIn(p.TAG, (root / 'gpu.vhd').read_text())

    def test_rejects_unknown_source(self):
        with self.assertRaises(ValueError):
            p.transform('nothing\n')


if __name__ == '__main__':
    unittest.main()
