import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_cd_dma_fast as p


class CdDmaFastTest(unittest.TestCase):
    def project(self, root, slow=p.SLOW_OLD, tie=p.TIE_OLD):
        root = Path(root)
        (root / 'sub').mkdir()
        (root / 'psx_mister_psx_top_resource.vhd').write_text('x\n' + tie + '\ny\n')
        (root / 'sub/dma.vhd').write_text('a\n' + slow + '\nb\n')
        (root / 'apf_top.qsf').write_text(f'set_global_assignment -name VHDL_FILE "{root}/sub/dma.vhd" -library work\n')
        return root

    def test_applies_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.project(d)
            p.apply(root); p.apply(root)
            self.assertIn("IGNORECDDMATIMING => '1',", (root / 'psx_mister_psx_top_resource.vhd').read_text())
            self.assertIn("ignoreCDTiming = '0') then -- CD access", (root / 'sub/dma.vhd').read_text())
            self.assertEqual((root / 'sub/dma.vhd').read_text().count('slowcnt  <= 24;'), 1)

    def test_duplicate_only_keeps_the_tie(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.project(d)
            p.apply(root, duplicate_only=True)
            self.assertIn("IGNORECDDMATIMING => '0',", (root / 'psx_mister_psx_top_resource.vhd').read_text())
            self.assertIn("toDevice = '0' and false) then -- CD access", (root / 'sub/dma.vhd').read_text())

    def test_rejects_unknown_source(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.project(d, slow='nothing here')
            with self.assertRaises(SystemExit):
                p.apply(root)


if __name__ == '__main__':
    unittest.main()
