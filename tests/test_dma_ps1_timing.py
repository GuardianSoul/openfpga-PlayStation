import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_dma_ps1_timing as p

DMA = ('a\n' + p.SIGNAL_OLD + 'b\n' + p.GPU_TRIGGER_OLD + p.SPU_TRIGGER_OLD + 'c\n' + p.LIST_END_OLD + 'd\n' + p.BLOCK_OLD + 'e\n')


class DmaPs1TimingTest(unittest.TestCase):
    def test_defaults(self):
        out = p.transform(DMA)
        self.assertEqual(p.transform(out), out)
        self.assertNotIn('rep_add   :=', out)
        self.assertIn('REP_target   <= REP_target + 3;', out)
        self.assertIn("(spu_dmaRequest = '1' and spuReqHeld = 28)", out)
        self.assertIn(p.GPU_TRIGGER_OLD, out)  # no GPU request delay
        self.assertNotIn('gpuReqHeld', out)

    def test_params(self):
        out = p.transform(DMA, block=2, list_end=0, spu_req_delay=80, gpu_req_delay=12)
        self.assertNotIn('ending on an empty node', out)
        self.assertIn('signal spuReqHeld          : integer range 0 to 80', out)
        self.assertIn("(spu_dmaRequest = '1' and spuReqHeld = 80)", out)
        self.assertNotIn(p.SPU_TRIGGER_OLD, out)
        self.assertIn("(gpu_dmaRequest = '1' and gpuReqHeld = 12)", out)
        self.assertIn('rep_add   := rep_add + 2 + 8;', out)

    def test_project(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'dma.vhd').write_text(DMA)
            (root / 'apf_top.qsf').write_text(f'set_global_assignment -name VHDL_FILE "{root}/dma.vhd" -library work\n')
            p.apply(root, spu_req_delay=10)
            self.assertIn('spuReqHeld = 10', (root / 'dma.vhd').read_text())

    def test_rejects_unknown_source(self):
        with self.assertRaises(ValueError):
            p.transform('no dma here\n')


if __name__ == '__main__':
    unittest.main()
