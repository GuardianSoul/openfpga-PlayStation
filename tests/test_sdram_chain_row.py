import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_sdram_chain_row as c
import prepare_ram_miss_return as m
from prepare_sdram_force_reg import force_reg_text
from prepare_sdram_idle_paths import idle_paths_text
from prepare_sdram_ref_owed import ref_owed_text

CTRL = Path(__file__).resolve().parents[1] / 'rtl/memory/psx_sdram_ctrl.sv'


class SdramChainRowTest(unittest.TestCase):
    def test_registers_compare_alone_and_after_miss_return(self):
        base = force_reg_text(idle_paths_text(ref_owed_text(CTRL.read_text())))
        for text in (base, m.controller_text(base)):
            out = c.controller_text(text)
            self.assertNotIn('fifo_adr[24:10] == addr[24:10]', out)
            self.assertIn('chain_row_q <= chain_head_row == addr[24:10];', out)
            self.assertIn('if (from_fifo && fifo_v && chain_row_q', out)
            with self.assertRaises(ValueError):
                c.controller_text(out)


if __name__ == '__main__':
    unittest.main()
