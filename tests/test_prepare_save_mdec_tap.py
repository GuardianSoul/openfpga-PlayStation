"""prepare_save_mdec_tap.py: the text transforms apply once, refuse a second pass, and keep what they must."""
from pathlib import Path
import re
import shutil
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_save_mdec_tap as step

MDEC = '''entity mdec is
   port
   (
      dma_readdata         : out std_logic_vector(31 downto 0);

      SS_reset             : in  std_logic
   );
end entity;
architecture arch of mdec is
begin
   RamYUVaddrB <= '0' & currentCoeff(5 downto 0) when (currentBlock >= 2 or rec_depth(1) = '0') else '1' & currentCoeff(5 downto 0);   -- mdec-mednafen: luminance table for monochrome
   scaleT1_address_b <= std_logic_vector(to_unsigned(idct_u  * 8 + idct_y, 6)) when (idctState = IDCT_STAGE1) else std_logic_vector(to_unsigned(idct_u  * 8 + idct_x, 6));
   scaleT2_address_b <= std_logic_vector(to_unsigned(idct_u1 * 8 + idct_y, 6)) when (idctState = IDCT_STAGE1) else std_logic_vector(to_unsigned(idct_u1 * 8 + idct_x, 6));
end architecture;
'''
EXP1 = '''    input  wire        spuram_done,
    // save-disc-set
    reg [8:0] ss_adr_1x = 0;
        if (reg_area && bus_addr[5]) bus_dataRead = bus_addr[4:0] == 5'd2 ? {7'd0, ss_busy} :
                                                    bus_addr[4:0] == 5'd3 ? {6'd0, disc_wrong, disc_settled} : 8'd0;   // save-disc-set
            spu_hold <= 1'b0; sp_st <= CP_IDLE; disc_silent <= 1'b0;
                4'd12: spu_hold <= bus_dataWrite[0];
'''


class Hardware(unittest.TestCase):
    def test_mdec(self):
        new = step.mdec(MDEC)
        self.assertEqual(new.count("when (save_tap_on = '1') else"), 2)
        self.assertIn('save_tap_yuv <= RamYUVdataB;', new)
        self.assertIn('save_tap_scale <= scaleT1_data_b;', new)
        self.assertIn('-- mdec-mednafen: luminance table for monochrome', new)            # the line's own comment stays
        # the second scale RAM and every line without the tap are untouched
        self.assertEqual([l for l in MDEC.splitlines() if 'scaleT2' in l], [l for l in new.splitlines() if 'scaleT2' in l])
        removed = [l for l in MDEC.splitlines() if l not in new.splitlines()]
        self.assertEqual(len(removed), 2)
        with self.assertRaises(ValueError):
            step.mdec(new)

    def test_mdec_refuses_a_changed_read_address(self):
        with self.assertRaises(ValueError):
            step.mdec(MDEC.replace('RamYUVaddrB <=', 'RamYUVaddrB <= x; RamYUVaddrB <='))
        with self.assertRaises(ValueError):
            step.mdec(MDEC.replace('idct_u  * 8 + idct_y', 'idct_u  * 8 + idct_z'))

    def test_exp1(self):
        new = step.exp1(EXP1)
        self.assertIn("4'd12: begin spu_hold <= bus_dataWrite[0]; mdec_tap <= bus_dataWrite[1]; end", new)
        self.assertEqual(new.count("spu_hold <= 1'b0; mdec_tap <= 1'b0;"), 1)
        # with save-core-reset (the E3 chain) the core reset's branch clears the tap too
        e3 = EXP1 + "    // save-core-reset\n                spu_hold <= 1'b0;\n"
        self.assertEqual(step.exp1(e3).count("spu_hold <= 1'b0; mdec_tap <= 1'b0;"), 2)
        with self.assertRaises(ValueError):
            step.exp1(EXP1 + "                spu_hold <= 1'b0;\n")
        self.assertIn("bus_addr[4:0] == 5'd2 ? {7'd0, ss_busy} :", new)
        self.assertIn("bus_addr[4:0] == 5'd3 ? {6'd0, disc_wrong, disc_settled} : 8'd0;", new)
        with self.assertRaises(ValueError):
            step.exp1(new)
        with self.assertRaises(ValueError):
            step.exp1(EXP1.replace('save-disc-set', 'x'))

    def test_hardware_faults_apply(self):
        texts = dict(mdec=step.mdec(MDEC), exp1=step.exp1(EXP1))
        for name, (kind, _, _) in step.HW_FAULTS.items():
            self.assertNotEqual(step.hw_fault(kind, texts[kind], name), texts[kind], name)

    def test_wiring(self):
        top = step.psx_top('      save_spuram_done : out std_logic\n   );\n      dma_readdata         => DMA_MDEC_read,\n')
        self.assertIn('save_tap_adr         => save_spu_adr(6 downto 0),', top)
        self.assertIn('save_mdec_q : out std_logic_vector(23 downto 0)\n   );', top)
        res = step.resource('    save_spuram_done : out std_logic\n);\n        save_spuram_done => save_spuram_done\n    );\n')
        self.assertIn('save_mdec_q => save_mdec_q\n    );', res)
        core = step.core_top('    wire [18:0] save_spuram_adr; wire [15:0] save_spuram_wdata, save_spuram_rdata;\n'
                             '        .cd_state(save_cd_state));\n(.save_spuram_done(save_spuram_done),.clk1x(clk1x));\n')
        self.assertIn('.mdec_tap(save_mdec_tap), .mdec_q(save_mdec_q),', core)
        self.assertIn('.save_mdec_tap(save_mdec_tap),.save_mdec_q(save_mdec_q),.clk1x(clk1x)', core)


@unittest.skipUnless(shutil.which('llvm-mc') and shutil.which('ld.lld'), 'llvm-mc / ld.lld needed')
class Routine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = step.big_build_text()
        cls.text = step.firmware_text(cls.base)

    def test_only_additions(self):
        import difflib
        rows = [l for l in difflib.unified_diff(self.base.splitlines(), self.text.splitlines(), n=0, lineterm='') if l[:3] not in ('+++', '---')]
        self.assertEqual([l for l in rows if l.startswith('-')], [])
        added = [l[1:].split('#')[0] for l in rows if l.startswith('+')]
        code = [l for l in added if re.match(r'(\d+:)?\s+[a-z]', l)]
        self.assertEqual(len(code), 36)                    # 18 table read + 5 flag + 13 load instructions (the three .equ lines start with a dot)
        with self.assertRaises(ValueError):
            step.firmware_text(self.text)

    def test_waits_applies_after_the_step(self):
        # waits is chained after the tap (and after early-load); its scratchpad part moves the line that
        # follows the tap's save block, so the other order is refused by the tap's anchor
        import prepare_save_waits as waits
        text = waits.firmware_text(self.text)
        self.assertIn('wait_dead', text)
        self.assertIn('mdec-tap', text)
        with self.assertRaises(ValueError):
            step.firmware_text(waits.firmware_text(self.base))
        # without that part the two steps touch different lines and commute
        parts = tuple(p for p in waits.PARTS if p != 'scratchpad')
        self.assertEqual(step.firmware_text(waits.firmware_text(self.base, parts)), waits.firmware_text(self.text, parts))

    def test_faults_apply(self):
        for name in step.FAULTS:
            self.assertNotEqual(step.fault_text(self.text, name), self.text, name)

    def test_blocks_on_the_mips_model(self):
        import run_save_mdec_tap as bench
        ok, rows = bench.routine_cases(None)
        self.assertTrue(ok, rows)
        _, quant, scale, _ = bench.tables(3)
        for name in step.FAULTS:
            writes, page, machine = bench.routine_round_trip(step.fault_text(self.text, name), quant, scale, 0x9684FFFF)
            good, _, _ = bench.routine_round_trip(self.text, quant, scale, 0x9684FFFF)
            self.assertTrue(writes != good or machine.tap_reads_off, name)


if __name__ == '__main__':
    unittest.main()
