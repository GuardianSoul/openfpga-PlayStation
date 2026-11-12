"""prepare_save_checksum.py, prepare_save_card_holdoff.py, prepare_save_checksum_routine.py: the text transforms."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_save_card_holdoff as holdoff
import prepare_save_checksum as checksum
import prepare_save_checksum_routine as routine


def fixture(pairs, head=''):
    """A text holding each anchor once, in order (what the steps need of the real files)."""
    return head + ''.join(f'// line {i}\n{old}' for i, (old, _) in enumerate(pairs)) + 'endmodule\n'


DEVICE = fixture(checksum.PAIRS, '// rd_seen disc_id_q\n')
TOP = fixture(checksum.TOP_PAIRS + holdoff.TOP_PAIRS)
SLOT = fixture(holdoff.PAIRS)
ROUTINE = fixture(routine.PAIRS)


class Checksum(unittest.TestCase):
    def test_device_transform_and_inverse(self):
        new = checksum.device_text(DEVICE)
        self.assertEqual(checksum.restore(new), DEVICE)
        self.assertIn("output reg         overflow_clear = 1'b0\n);", new)
        # the sum before the clear (the clear wins), both before the aux grant moves
        self.assertLess(new.index('if (cs_v) cs <='), new.index("cs <= 32'd0;"))
        # aux_dout drives the cs_w register only (RC #1312 timing), not the adder
        self.assertIn('cs_w <= cp_dir ? aux_dout : cp_q;', new)
        self.assertEqual([l for l in new.splitlines() if 'aux_dout' in l and not l.strip().startswith('//')],
                         ['        cs_w <= cp_dir ? aux_dout : cp_q;'])
        self.assertLess(new.index("cs <= 32'd0;"), new.index("if (up_go) begin if (aux_done)"))
        # cs is declared before the read mux uses it
        self.assertLess(new.index('reg [31:0] cs ='), new.index('bus_addr[4] ? cs_q'))
        with self.assertRaises(ValueError):
            checksum.device_text(new)
        with self.assertRaises(ValueError):
            checksum.device_text(DEVICE.replace('rd_seen', 'x'))

    def test_every_fault_applies_once(self):
        new = checksum.device_text(DEVICE)
        for name in checksum.FAULTS:
            self.assertNotEqual(checksum.fault_text(new, name), new, name)

    def test_top_and_sdc(self):
        new = holdoff.top_text(checksum.top_text(TOP))
        self.assertIn('.overflow_clear(save_ovf_clear)', new)
        self.assertIn('.overflow(memcard_overflow), .overflow_clear(save_ovf_clear));', new)
        self.assertIn('.park_req(!reset_n),.park(vram_park));', new)
        self.assertNotIn('vram_park=!reset_n', new)
        sdc = checksum.sdc_text('set_false_path -to [get_registers {*|psx_save_exp1:save_exp1|s2x_req[0]}]\n')
        self.assertIn('save_exp1|s2x_ovf[0]', sdc)
        with self.assertRaises(ValueError):
            checksum.sdc_text(sdc)


class SlotOverflow(unittest.TestCase):
    def test_slot_scope(self):
        slot = fixture(checksum.SLOT_PAIRS + holdoff.PAIRS)
        new = holdoff.slot_text(checksum.slot_text(slot))
        self.assertIn('if (wr_strobe && in_save && pending) overflow', new)
        self.assertEqual(checksum.slot_restore(holdoff.restore(new)), slot)
        with self.assertRaises(ValueError):
            checksum.slot_text(checksum.slot_text(slot))
        for pairs in checksum.SLOT_FAULTS.values():
            for a, _ in pairs:
                self.assertEqual(checksum.slot_text(slot).count(a), 1)


class Holdoff(unittest.TestCase):
    def test_slot_transform_and_inverse(self):
        new = holdoff.slot_text(SLOT)
        self.assertEqual(holdoff.restore(new), SLOT)
        self.assertIn("output reg         park = 1'b1\n);", new)
        self.assertIn('parameter integer HOLD_BITS = 16,', new)
        with self.assertRaises(ValueError):
            holdoff.slot_text(new)
        for name in holdoff.FAULTS:
            self.assertNotEqual(holdoff.fault_text(new, name), new, name)


class Routine(unittest.TestCase):
    def test_routine_text(self):
        new = routine.firmware_text(ROUTINE)
        # save: page 1's sum kept below the bitmap, the stream's after cz_save, page 0 put twice (T last)
        self.assertLess(new.index('lw      $t2, CSUM($k0)          # checksum: page 1'), new.index('jal     cz_save'))
        self.assertLess(new.index('jal     cz_save'), new.index('lw      $s0, CSUM($k0)'))
        self.assertLess(new.index('sw      $zero, CS_LAST($k0)'), new.index('sw      $s0, CS_LAST($k0)'))
        self.assertEqual(new.count('sw      $s0, CS_LAST($k0)\n        jal     put_page'), 1)
        # load: pages 1 and 0 summed first, the compare before cz_done; no pages-as-they-are path
        self.assertLess(new.index('li      $a0, CPU_PAGE\n        lw      $v0, CSUM($k0)'), new.index('jal     cz_load'))
        self.assertLess(new.index('bne     $t0, $v0, bad'), new.index('cz_done:'))
        self.assertNotIn('pages as they are', new)
        import re
        # an instruction line, with or without a label in front (`1:      beq ...`)
        instructions = lambda t: sum(1 for l in t.splitlines() if re.match(r'^(\w+:)?\s+[a-z]', l) and not re.match(r'^(\w+:)?\s+\.', l))
        self.assertEqual(instructions(new) - instructions(ROUTINE), 17)
        with self.assertRaises(ValueError):
            routine.firmware_text(new)
        for name in routine.FAULTS:
            self.assertNotEqual(routine.fault_text(new, name), new, name)


if __name__ == '__main__':
    unittest.main()
