import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_dma_io_tail as p

MUX = p.UPSTREAM / 'rtl/memorymux.vhd'
TOP = p.UPSTREAM / 'rtl/psx_top.vhd'


class DmaIoTailTest(unittest.TestCase):
    def test_patch_gates_isidle_and_connects_request(self):
        mux = p.memorymux_text(MUX.read_text(), 7)
        self.assertIn("dmaRequest           : in  std_logic := '0';", mux)
        self.assertIn("and dmaio_seen = '0' and dmaio_gate = 0) else '0';", mux)
        self.assertIn('dmaio_gate <= 6;', mux)
        self.assertIn(p.IO_READ + ' and ' + p.NOT_DMA_REG, mux)
        top = p.psx_top_text(TOP.read_text())
        self.assertIn('      dmaRequest           => dmaRequest, -- dma-io-tail\n', top)

    def test_rejects_double_patch_and_bad_gate(self):
        with self.assertRaises(ValueError):
            p.memorymux_text(p.memorymux_text(MUX.read_text()))
        with self.assertRaises(ValueError):
            p.psx_top_text(p.psx_top_text(TOP.read_text()))
        with self.assertRaises(ValueError):
            p.memorymux_text(MUX.read_text(), 0)

    def test_rejects_changed_source(self):
        with self.assertRaises(ValueError):
            p.memorymux_text(MUX.read_text().replace('writeFifo_Wr_1 <= writeFifo_Wr;', 'x'))

    def test_accepts_fast_bios_reset_line(self):
        text = MUX.read_text().replace("            ext_lastactive   <= '0';\n",
                                       "            ext_lastactive   <= '0';\n"
                                       "            fast_bios        <= '1';  -- PSXEarly --fast-bios\n")
        self.assertIn("dmaio_gate       <= 0;", p.memorymux_text(text))

    def test_prepare_project_retargets_both_files(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'src').mkdir()
            (d / 'src/memorymux.vhd').write_text(MUX.read_text())
            (d / 'src/psx_top.vhd').write_text(TOP.read_text())
            qsf = d / 'apf_top.qsf'
            qsf.write_text('set_global_assignment -name VHDL_FILE "src/memorymux.vhd" -library work\n'
                           'set_global_assignment -name VHDL_FILE "src/psx_top.vhd" -library work\n')
            result = p.prepare_project(qsf, d / 'out', 5)
            text = qsf.read_text()
            self.assertIn(str(d / 'out/memorymux.vhd'), text)
            self.assertIn(str(d / 'out/psx_top.vhd'), text)
            self.assertEqual(result['gate'], 5)
            self.assertIn('dmaio_gate <= 4;', (d / 'out/memorymux.vhd').read_text())


if __name__ == '__main__':
    unittest.main()
