import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_dma_otc as p

DMA = 'a\n                        ' + p.OLD + 'b\n'


class DmaOtcTest(unittest.TestCase):
    def test_transform(self):
        out = p.transform(DMA)
        self.assertEqual(p.transform(out), out)
        self.assertIn('if (channel = 6) then', out)
        self.assertIn('x"40000000") or to_unsigned(2, 13);', out)

    def test_project(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'dma.vhd').write_text(DMA)
            (root / 'apf_top.qsf').write_text(f'set_global_assignment -name VHDL_FILE "{root}/dma.vhd" -library work\n')
            p.apply(root)
            self.assertIn(p.TAG, (root / 'dma.vhd').read_text())

    def test_rejects_unknown_source(self):
        with self.assertRaises(ValueError):
            p.transform('no chcr ram here\n')


if __name__ == '__main__':
    unittest.main()
