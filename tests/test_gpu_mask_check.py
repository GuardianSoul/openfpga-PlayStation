import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_gpu_mask_check as p

BE = ('                        vram_BE         <= fifoOut_Dout(84) & fifoOut_Dout(84) & fifoOut_Dout(83) & fifoOut_Dout(83)'
      ' & fifoOut_Dout(82) & fifoOut_Dout(82) & fifoOut_Dout(81) & fifoOut_Dout(81);\n')


def source(name):
    text = ''.join('x\n' + old for _, old, _ in p.EDITS[name]) + 'y\n'
    return text + BE if name == 'gpu.vhd' else text


class GpuMaskCheckTest(unittest.TestCase):
    def test_copy_leaves_the_check_to_the_write_port(self):
        out = p.transform('gpu_vram2vram.vhd', source('gpu_vram2vram.vhd'))
        self.assertNotIn('vramLineData(15)', out)
        self.assertIn("pixelWrite <= '1'; -- gpu-mask-check", out)
        self.assertIn('resize(dstY - srcY, 10) < heig', out)

    def test_write_port_reads_then_clears_masked_byte_enables(self):
        out = p.transform('gpu.vhd', source('gpu.vhd'))
        self.assertIn('vram_DOUT(63) & vram_DOUT(47) & vram_DOUT(31) & vram_DOUT(15)', out)
        self.assertIn('and not (maskUse(3) & maskUse(3)', out)
        self.assertIn("(fifoOut_Dout(86) = '0' or maskReady = '1')", out)
        self.assertIn("reqVRAMStore  <= '0';", out)
        self.assertIn("elsif (fifoOut_Empty = '0') then\n                        maskReady <= '0';\n", out)
        self.assertIn('GPUSTAT_DrawPixelsMask and (not pipeline_pixelWrite)', out)
        self.assertIn('"01" & "1111"', out)

    def test_rejects_changed_source_and_double_patch(self):
        for name in p.EDITS:
            with self.assertRaises(ValueError):
                p.transform(name, p.transform(name, source(name)))
            for _, old, _ in p.EDITS[name]:
                with self.assertRaises(ValueError):
                    p.transform(name, source(name).replace(old, ''))
        with self.assertRaises(ValueError):
            p.transform('gpu.vhd', source('gpu.vhd').replace(BE, ''))

    def test_project(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            lines = ''
            for name in p.EDITS:
                (root / name).write_text(source(name))
                lines += f'set_global_assignment -name VHDL_FILE "{root}/{name}" -library work\n'
            (root / 'apf_top.qsf').write_text(lines)
            self.assertEqual(len(p.apply(root)), 2)
            self.assertIn('READMASK', (root / 'gpu.vhd').read_text())


if __name__ == '__main__':
    unittest.main()
