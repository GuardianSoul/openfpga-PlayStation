# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import prepare_save_wake_hold as step

DEVICE = ("    reg trap_tout = 1'b0;\n"
          "    reg [2:0] s2x_core = 3'b000;\n"
          "            trap_age <= 0; trap_tout <= 1'b0;   // save-trap-timeout\n"
          "                want_load <= req_load_74; want_save <= !req_load_74; trap <= 1'b1;\n"
          "            trap_age <= trap ? trap_age + {{TRAP_AGE_BITS{1'b0}}, !core_paused} : 0;\n")


class WakeHold(unittest.TestCase):
    def test_gate(self):
        out = step.device_text(DEVICE)
        self.assertIn('!core_paused && !wake_req}', out)
        self.assertNotIn('s2x_core[1] &&', out)
        self.assertIn('wake_req <= req_load_74 && s2x_core[1];', out)
        # test4's device (part 1 only) gets part 2 the same way
        self.assertEqual(step.device_text(DEVICE.replace(step.OLD, step.GATE1)), out)
        first = out.replace(step.NEW, step.PART2)   # test5's first fit
        self.assertEqual(step.device_text(first), out)
        with self.assertRaises(ValueError):
            step.device_text(out)

    def test_part2_release_form(self):
        # 0.5.10 = test9 = T5 s3 carries test5's first form: reset gate and wake exemption together
        out = step.device_text_part2(DEVICE)
        self.assertIn(step.PART2, out)
        self.assertIn('wake_req <= req_load_74 && s2x_core[1];', out)
        self.assertEqual(step.device_text_part2(DEVICE.replace(step.OLD, step.GATE1)), out)
        self.assertEqual(out.replace(step.PART2, step.NEW), step.device_text(DEVICE))
        for done in (out, step.device_text(DEVICE)):
            with self.assertRaises(ValueError):
                step.device_text_part2(done)

    def test_needs_core_reset_sync(self):
        with self.assertRaises(ValueError):
            step.device_text(DEVICE.replace("    reg [2:0] s2x_core = 3'b000;\n", ''))

    def test_faults(self):
        out = step.device_text(DEVICE)
        self.assertIn(step.OLD, step.fault_text(out, 'no-exempt'))
        self.assertIn(step.GATE1, step.fault_text(out, 'gate-only'))
        self.assertIn('!core_paused && wake_req}', step.fault_text(out, 'exempt-inverted'))
        self.assertIn('wake_req <= req_load_74;   //', step.fault_text(out, 'exempt-any-load'))


if __name__ == '__main__':
    unittest.main()
