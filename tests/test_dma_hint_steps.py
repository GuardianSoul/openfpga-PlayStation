import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_dma_miss_return as d
import prepare_ram_miss_return as m
import prepare_ram_read_hint as h
import prepare_sdram_chain_row as c
import prepare_sdram_write_hits as wh
import prepare_sdram_early_refresh as er
from prepare_sdram_force_reg import force_reg_text
from prepare_sdram_idle_paths import idle_paths_text
from prepare_sdram_ref_owed import ref_owed_text

CTRL = Path(__file__).resolve().parents[1] / 'rtl/memory/psx_sdram_ctrl.sv'


class DmaHintStepsTest(unittest.TestCase):
    def base(self):
        return force_reg_text(idle_paths_text(ref_owed_text(CTRL.read_text())))

    def test_dma_return_alone_and_stacked(self):
        for text in (self.base(), m.controller_text(self.base())):
            out = d.controller_text(text)
            self.assertIn('assign dma_wr = dma_miss_return ? x_dma_wr : dma_wr_q;', out)
            self.assertEqual(out.count('dma_miss_return <= 0;'), 1)
            with self.assertRaises(ValueError):
                d.controller_text(out)

    def test_hint_needs_miss_return_and_stacks(self):
        with self.assertRaises(ValueError):
            h.controller_text(self.base())
        out = c.controller_text(h.controller_text(d.controller_text(m.controller_text(self.base()))))
        self.assertIn('input  wire         ch1_hint_req,', out)
        self.assertIn('hint_precharged <= 1;', out)
        self.assertIn('miss_read_return <= hint_precharged && hint_addr[26:2] == ch1_addr[26:2]', out)
        self.assertNotIn('miss_read_return <= 0; end', out)

    def test_full_stack_in_order(self):
        t = self.base()
        for step in (m, d, wh, h, er, c):
            t = step.controller_text(t)
        self.assertIn('stf <= row_state(head_adr);', t)
        self.assertIn('early_pending || early_quiet;', t)
        self.assertIn('!hint_v;', t)
        self.assertIn('early_quiet_q <= early_quiet_c;', t)
        self.assertIn('hint_ref_ok <= ref_timer > 4', t)

    def test_hint_plumbing(self):
        self.assertIn('if rising_edge(clk1x) then', h.psx_top_text(
            '      ram_refresh           : out std_logic;\n   ram_refresh   <= reset_intern;\n'))
        self.assertIn('ram_hint_adr => ram_hint_adr', h.resource_text(
            '    ram_refresh : out std_logic;\n        ram_refresh => ram_refresh,\n'))


if __name__ == '__main__':
    unittest.main()
