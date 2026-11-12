"""prepare_save_early_load.py: the routine step applies once, part by part, its planted faults apply, and the
routine's own load-decision code behaves as designed on the MIPS model (scripts/run_save_early_load.py)."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_save_early_load as step
from tool_paths import find_tool

try:
    BASE = step.base_text()
except Exception as error:   # the reference checkouts the routine text is built from are not here
    BASE = None
    WHY = str(error)
TOOLS = all(find_tool(n) or __import__('shutil').which(n) for n in ('llvm-mc', 'ld.lld', 'llvm-objcopy', 'llvm-nm'))


@unittest.skipIf(BASE is None, 'routine base text not available')
class Text(unittest.TestCase):
    def test_applies_once(self):
        new = step.firmware_text(BASE)
        self.assertNotEqual(new, BASE)
        with self.assertRaises(ValueError):
            step.firmware_text(new)

    def test_parts_alone_and_together(self):
        for part in step.PARTS:
            text = step.firmware_text(BASE, (part,))
            self.assertIn(f'early-load {part}', text)
            for other in step.PARTS:
                if other != part:
                    self.assertNotIn(f'early-load {other}', text)
        with self.assertRaises(ValueError):
            step.firmware_text(BASE, ('nothing',))

    def test_only_additions_outside_the_changed_blocks(self):
        new = step.firmware_text(BASE).splitlines()
        gone = [l for l in BASE.splitlines() if l not in new]
        # the answer at the end, the 2,000 Getstat loop, load_start's ra save and comment lines: nothing else leaves
        self.assertLessEqual(len(gone), 12, gone)

    def test_one_answer(self):
        new = step.firmware_text(BASE)
        # FINISH writes: the early OK and the DIAG delay slot come, the load's own at its end goes
        write = 'sb      $t0, FINISH($k0)'
        self.assertEqual(new.count(write), BASE.count(write) + 1)
        self.assertIn('        b       answer\n        li      $t0, 1\n', new)
        self.assertEqual(new.count('\nanswer:'), 1)

    def test_magic_and_layout_unchanged(self):
        new = step.firmware_text(BASE)
        for line in BASE.splitlines():
            if '.equ' in line:
                self.assertIn(line, new)
        self.assertEqual(step.NAME_WORD, 193)       # behind the MDEC tables (128-191) and MT_FLAG (192)

    def test_ready_rounds(self):
        # 1,024 rounds: a drive a remount has just put into cd_top's new-disc sequence (up to 38.2M clocks)
        # is waited for; routine c had 8 (15 ms) and refused it
        new = step.firmware_text(BASE)
        self.assertEqual(step.READY_ROUNDS, 1024)
        self.assertGreaterEqual(step.READY_ROUNDS * 64000, 25000000 + 44100 * 300)
        self.assertIn('        .equ    READY_ROUNDS, 1024\n', new)
        self.assertIn('9:      li      $s6, READY_ROUNDS ', new)
        self.assertNotIn('BENCH-ONLY', new)
        bench = step.firmware_text(BASE, step.PARTS, 8)
        self.assertIn('        .equ    READY_ROUNDS, 8   # BENCH-ONLY OVERRIDE (the routine has 1024)\n', bench)
        import os
        os.environ['EARLY_LOAD_BENCH_ROUNDS'] = '8'
        try:
            self.assertIn('BENCH-ONLY', step.firmware_text(BASE))
            self.assertNotIn('BENCH-ONLY', step.firmware_text(BASE, step.PARTS, step.READY_ROUNDS))   # what build() asks for
        finally:
            del os.environ['EARLY_LOAD_BENCH_ROUNDS']

    def test_faults_apply(self):
        new = step.firmware_text(BASE)
        for name in step.FAULTS:
            self.assertNotEqual(step.fault_text(new, name), new, name)
        self.assertEqual(set(step.FAULTS), set(step.FAULT_PART))


@unittest.skipIf(BASE is None or not TOOLS, 'routine base text or the MIPS tools not available')
class Model(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import run_save_early_load as bench
        cls.bench = bench
        cls.new = step.firmware_text(BASE)

    def test_cases_pass(self):
        failed = [(n, d) for n, p, d in self.bench.checks(self.new) if not p]
        self.assertEqual(failed, [])

    def test_today_fails_the_disc_switch(self):
        rows = {n: (p, d) for n, p, d in self.bench.checks(BASE)}
        passed, detail = rows['TODAY other disc, served only after the answer: times out, error']
        self.assertTrue(passed)
        self.assertEqual(detail['finishes'], [2])

    def test_faults_caught(self):
        for name in step.FAULTS:
            failed = [n for n, p, _ in self.bench.checks(step.fault_text(self.new, name)) if not p]
            self.assertTrue(failed, name)


if __name__ == '__main__':
    unittest.main()
