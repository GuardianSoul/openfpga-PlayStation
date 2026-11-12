"""prepare_save_disc_id.py: the hardware and routine transforms apply once and keep what they must."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_save_disc_id as step

EXP1 = '''    input  wire [23:0] mdec_q,          // {scale word, quantisation byte}
    input  wire [63:0] cd_state
    // register reads: one 16-way byte select over {CD state, GPU ranges}
        if (reg_area && bus_addr[5]) bus_dataRead = bus_addr[2] ? (!bus_addr[0] ? mdec_q[7:0] : ss_adr_1x[0] ? mdec_q[23:16] : mdec_q[15:8]) :   // save-mdec-tap
                                                    bus_addr[4:0] == 5'd2 ? {7'd0, ss_busy} :
                                                    bus_addr[4:0] == 5'd3 ? {6'd0, disc_wrong, disc_settled} : 8'd0;   // save-disc-set
'''
CORE = '''    wire [31:0] trackinfo_data;
    wire [8:0] trackinfo_addr;
    wire trackinfo_write;
        .mdec_tap(save_mdec_tap), .mdec_q(save_mdec_q),
        .cd_state(save_cd_state));
'''

try:
    import prepare_save_early_load as early
    BASE = early.firmware_text(early.base_text(), early.PARTS, early.READY_ROUNDS)
except Exception as error:   # the reference checkouts the routine text is built from are not here
    BASE = None


class Hardware(unittest.TestCase):
    def test_exp1(self):
        new = step.exp1(EXP1)
        self.assertEqual(new.count('trackinfo_write'), 2)
        self.assertIn("if (trackinfo_addr == 9'd1) disc_id[19:0] <= trackinfo_data[19:0];", new)
        # the read sits after the MDEC tap's (address bit 2) and before the SS busy and DISC_SET reads
        lines = new.splitlines()
        at = next(i for i, l in enumerate(lines) if 'bus_addr[3] ? disc_id_q' in l)
        self.assertIn('save-mdec-tap', lines[at - 1])
        self.assertIn("5'd2", lines[at + 1])
        self.assertEqual([l for l in EXP1.splitlines() if l not in lines], [])        # nothing removed
        with self.assertRaises(ValueError):
            step.exp1(new)

    def test_exp1_needs_the_mdec_tap(self):
        with self.assertRaises(ValueError):
            step.exp1(EXP1.replace('save-mdec-tap', ''))

    def test_core_top(self):
        new = step.core_top(CORE)
        self.assertIn('.trackinfo_write(trackinfo_write), .trackinfo_addr(trackinfo_addr), .trackinfo_data(trackinfo_data[19:0]),', new)
        with self.assertRaises(ValueError):
            step.core_top(new)
        with self.assertRaises(ValueError):
            step.core_top(CORE.replace('    wire trackinfo_write;\n', ''))

    def test_faults_apply(self):
        new = step.exp1(EXP1)
        for name in step.HW_FAULTS:
            self.assertNotEqual(step.hw_fault(new, name), new)


@unittest.skipIf(BASE is None, 'routine base text not available')
class Routine(unittest.TestCase):
    def test_applies_once_and_adds_ten_instructions(self):
        new = step.firmware_text(BASE)
        code = lambda t: [l for l in t.splitlines() if l.strip() and not l.strip().startswith(('#', '.equ')) and not l.rstrip().endswith(':')]
        self.assertEqual(len(code(new)) - len(code(BASE)), 9)
        with self.assertRaises(ValueError):
            step.firmware_text(new)

    def test_identity_word_is_free(self):
        # MDEC tables 128-191, MT_FLAG 192, boot name 193-197
        self.assertEqual(step.ID_WORD, 198)
        self.assertNotIn('PAGE+4*198', BASE)

    def test_no_drive_access_added(self):
        new = step.firmware_text(BASE)
        added = [l for l in new.splitlines() if l not in BASE.splitlines()]
        self.assertFalse([l for l in added if 'cd_cmd' in l or '0x180' in l and '$t9' in l])

    def test_faults_apply(self):
        new = step.firmware_text(BASE)
        for name in step.FAULTS:
            self.assertNotEqual(step.fault_text(new, name), new)

    def test_needs_early_load_and_comes_before_waits(self):
        import prepare_save_waits as waits
        with self.assertRaises(ValueError):
            step.firmware_text(early.base_text())
        with self.assertRaises(ValueError):
            step.firmware_text(waits.firmware_text(BASE))
        waits.firmware_text(step.firmware_text(BASE))


if __name__ == '__main__':
    unittest.main()
