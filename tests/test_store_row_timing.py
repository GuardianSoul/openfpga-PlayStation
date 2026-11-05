import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_store_row_timing as p
import prepare_dma_ps1_timing as dma_step

MUX = ''.join('x\n' + old for _, old, _ in p.MEMORYMUX_EDITS) + 'y\n'
DMA = dma_step.transform('a\n' + dma_step.SIGNAL_OLD + 'b\n' + dma_step.GPU_TRIGGER_OLD + dma_step.SPU_TRIGGER_OLD
                         + 'c\n' + dma_step.LIST_END_OLD + 'd\n' + dma_step.BLOCK_OLD + 'e\n')


class StoreRowTimingTest(unittest.TestCase):
    def test_page_compare_covers_the_mirror_bits(self):
        out = p.memorymux_text(MUX)
        self.assertIn('ram_page_addr          : unsigned(12 downto 0)', out)
        self.assertIn('ram_page_addr <= mem_addressData(22 downto 10);', out)
        self.assertNotIn('mem_addressData(20 downto 10) = ram_page_addr)', out)
        self.assertNotIn('mem_addressData(20 downto 10) /= ram_page_addr', out)
        self.assertIn('mem_addressData(20 downto 11) = ram_page_addr(10 downto 1)', out)

    def test_row_wait_is_2_mirror_plus_1_and_2_in_9_plus_1(self):
        out = p.memorymux_text(MUX)
        self.assertIn('if (store_row_misses = 3 or store_row_misses = 7) then waitcnt <= 4; else waitcnt <= 3; end if;', out)
        self.assertIn('if (store_row_misses = 3 or store_row_misses = 7) then waitcnt <= 3; else waitcnt <= 2; end if;', out)
        self.assertIn("if (writeFifo_Empty = '1') then -- direct, not queued\n                                       waitcnt <= 3;\n", out)
        self.assertIn("if (store_row_misses = 8) then store_row_misses <= (others => '0');", out)
        self.assertIn("store_row_misses <= (others => '0'); -- store-row-timing\n            ext_lastactive", out)

    def test_memorymux_rejects_changed_source_and_double_patch(self):
        with self.assertRaises(ValueError):
            p.memorymux_text(p.memorymux_text(MUX))
        for _, old, _ in p.MEMORYMUX_EDITS:
            with self.assertRaises(ValueError):
                p.memorymux_text(MUX.replace(old, ''))

    def test_spu_hold_moves_every_use(self):
        out = p.dma_text(DMA)
        self.assertEqual(out, dma_step.transform('a\n' + dma_step.SIGNAL_OLD + 'b\n' + dma_step.GPU_TRIGGER_OLD
                                                 + dma_step.SPU_TRIGGER_OLD + 'c\n' + dma_step.LIST_END_OLD + 'd\n'
                                                 + dma_step.BLOCK_OLD + 'e\n', spu_req_delay=32))
        with self.assertRaises(ValueError):
            p.dma_text(out)

    def test_project(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'orig').mkdir()
            (root / 'orig/memorymux.vhd').write_text(MUX)
            (root / 'dma.vhd').write_text(DMA)
            (root / 'apf_top.qsf').write_text(
                f'set_global_assignment -name VHDL_FILE "{root.resolve()}/orig/memorymux.vhd" -library work\n'
                f'set_global_assignment -name VHDL_FILE "{root.resolve()}/dma.vhd" -library work\n')
            p.apply(root)
            qsf = (root / 'apf_top.qsf').read_text()
            self.assertIn('store-row-timing/memorymux.vhd', qsf)
            self.assertNotIn('orig/memorymux.vhd', qsf)
            self.assertEqual((root / 'orig/memorymux.vhd').read_text(), MUX)
            self.assertIn('spuReqHeld = 32', (root / 'dma.vhd').read_text())
            self.assertIn('store_row_misses', (root / 'store-row-timing/memorymux.vhd').read_text())


if __name__ == '__main__':
    unittest.main()
