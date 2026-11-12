"""prepare_save_readout_hold_routine.py: the routine transform, its numbers and the wait loop's rule."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_save_readout_hold_routine as step

ROUTINE = '''# mdec-tap
restore:                                # staging page holds the registers to return with
        jal     pack_end                # page 0 with the bitmap and size, scratchpad back
        nop
        li      $t0, 1
        sb      $t0, FINISH($k0)
        jal     get_page                # the registers back into the staging page
        move    $a0, $zero
        STUB    restore
        jal     cd_resume               # Setloc + ReadN / Play, IRQENA back
        nop
        b       answer
        li      $t0, 1
'''


def windows(reads, total=40, left=10):
    """The loop's rule as the assembly computes it (32-bit unsigned compare): reads[i] = window i had a
    read -> the number of windows waited."""
    t1, t2, n = total, left, 0
    while True:
        seen = reads(n); n += 1
        t1 -= 1
        if seen:
            t2 = (t1 - 1) & 0xFFFFFFFF
        if not t2 < t1:
            return n


class Routine(unittest.TestCase):
    def test_text(self):
        new = step.firmware_text(ROUTINE)
        added = [l for l in new.splitlines() if l not in ROUTINE.splitlines()]
        code = [l for l in added if not l.lstrip().startswith('#') and not l.rstrip().endswith(':')]
        self.assertEqual(len(code), 14)          # + one "li $t0, 1" the anchor also has: 15 instructions, 60 bytes
        self.assertLess(new.index('sb      $t0, FINISH($k0)'), new.index('rh_hold:'))
        self.assertLess(new.index('rh_quiet:'), new.index('jal     get_page'))
        self.assertIn('        li      $t1, 40\n        li      $t2, 10\n', new)
        self.assertIn('        lui     $t3, 0x0184', new)
        with self.assertRaises(ValueError):
            step.firmware_text(new)
        with self.assertRaises(ValueError):          # before mdec-tap
            step.firmware_text(ROUTINE.replace('mdec-tap', 'x'))
        with self.assertRaises(ValueError):          # two FINISH places of that shape
            step.firmware_text(ROUTINE + ROUTINE)

    def test_numbers(self):
        hi, total, left = step.numbers()
        self.assertEqual((hi, total, left), (0x184, 40, 10))
        self.assertAlmostEqual((hi << 16) * step.ROUND_CYCLES / step.CLK1X, 1.5, delta=0.01)
        self.assertEqual(step.numbers(1.0, 3, 45)[1:], (45, 42))
        with self.assertRaises(ValueError):
            step.numbers(0.5, 45, 60)                # 120 windows: the "seen" mark is the unsigned compare, 63 at most

    def test_bench_window_and_faults(self):
        new = step.firmware_text(ROUTINE)
        bench = step.bench_window(new)
        self.assertIn(f'li      $t3, {step.BENCH_ROUNDS} ', bench)
        self.assertEqual(len(bench.splitlines()), len(new.splitlines()))
        for name in step.FAULTS:
            self.assertNotEqual(step.fault_text(bench, name), bench, name)

    def test_rule(self):
        self.assertEqual(windows(lambda n: False), 30)                 # never a read: the no-read limit
        self.assertEqual(windows(lambda n: True), 40)                  # reads without end: the limit
        self.assertEqual(windows(lambda n: 3 <= n < 8), 9)             # reads in windows 3-7: out after the first quiet one
        self.assertEqual(windows(lambda n: n == 0), 2)
        self.assertEqual(windows(lambda n: n == 29), 31)               # the first read in the last window before the no-read limit
        self.assertEqual(windows(lambda n: n >= 35), 30)               # reads only after the no-read limit: not waited for
        self.assertEqual(windows(lambda n: n in (2, 4)), 4)            # one quiet window between reads ends it (quiet = one window)
        self.assertEqual(windows(lambda n: n >= 20), 40)
        for total, left in ((45, 42), (63, 1), (2, 1)):
            self.assertEqual(windows(lambda n: False, total, left), total - left)
            self.assertEqual(windows(lambda n: True, total, left), total)


if __name__ == '__main__':
    unittest.main()
