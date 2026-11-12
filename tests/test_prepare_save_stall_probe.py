"""prepare_save_stall_probe.py (EXPERIMENT routine): the probe applies once on routine c2, adds only its
block, count word and table, its planted faults apply and are caught, and the per-save behaviour holds on the
MIPS model (scripts/run_save_stall_probe.py); the CPU bench's busy-time check tells a good run from a bad one."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_save_stall_probe as step
import save_stall_probe_bench as cpu
from tool_paths import find_tool

try:
    BASE = step.base_text()
except Exception as error:   # the reference checkouts the routine text is built from are not here
    BASE = None
TOOLS = all(find_tool(n) or __import__('shutil').which(n) for n in ('llvm-mc', 'ld.lld', 'llvm-objcopy', 'llvm-nm'))


class Numbers(unittest.TestCase):
    def test_table(self):
        self.assertEqual(step.DELAYS[:2], (0, 0))                 # save 1 (with the unpack) and save 2: no wait
        self.assertEqual(list(step.DELAYS), sorted(step.DELAYS))
        self.assertTrue(all(0 <= d < 256 for d in step.DELAYS))
        ms = [1000 * step.rounds(n) * step.ROUND_CYCLES / step.CLK1X for n in range(1, step.PROBES + 1)]
        for got, want in zip(ms[2:], (50, 150, 400, 1500)):
            self.assertLess(abs(got - want) / want, 0.1)
        self.assertIsNone(step.rounds(step.PROBES + 1))

    def rows(self, busy):
        rows = [dict(save=n + 1, busy=b, stored=1, size=2048, magic='00000000', finish=1) for n, b in enumerate(busy)]
        return rows + [dict(save=len(busy) + 1, busy=150000, stored=24, size=24, magic='70535839', finish=1)]

    def test_cpu_bench_check(self):
        n = [step.rounds(s, cpu.BENCH_SHIFT) - 1 for s in range(1, step.PROBES + 1)]
        good = [900000] + [3000 + 2 * r for r in n[1:]]
        found, calibration = cpu.problems(self.rows(good))
        self.assertEqual(found, [])
        self.assertEqual(calibration['round_cycles'], 2)
        shifted = [900000] + [3000 + 2 * r for r in n[2:]] + [3000]          # the table read one entry on
        self.assertTrue(cpu.problems(self.rows(shifted))[0])
        rows = self.rows(good); rows[2]['magic'] = '70535839'
        self.assertTrue(cpu.problems(rows)[0])
        rows = self.rows(good); rows[-1]['stored'] = 1
        self.assertTrue(cpu.problems(rows)[0])
        self.assertTrue(cpu.problems(self.rows(good)[:-1])[0])


@unittest.skipIf(BASE is None, 'routine base text not available')
class Text(unittest.TestCase):
    def test_applies_once_on_c2(self):
        new = step.firmware_text(BASE)
        self.assertNotEqual(new, BASE)
        with self.assertRaises(ValueError):
            step.firmware_text(new)

    def test_only_additions(self):
        new = step.firmware_text(BASE).splitlines()
        self.assertEqual([l for l in BASE.splitlines() if l not in new], [])
        self.assertEqual(len(new) - len(BASE.splitlines()), 28)

    def test_bench_shift_changes_one_line(self):
        a, b = step.firmware_text(BASE).splitlines(), cpu.firmware_text(BASE).splitlines()
        self.assertEqual([(x, y) for x, y in zip(a, b) if x != y][0][1].split('#')[0].split(), ['.equ', 'PROBE_SHIFT,', str(cpu.BENCH_SHIFT)])
        self.assertEqual(len([1 for x, y in zip(a, b) if x != y]), 1)

    def test_faults_apply(self):
        new = step.firmware_text(BASE)
        for name in step.FAULTS:
            self.assertNotEqual(step.fault_text(new, name), new, name)


@unittest.skipIf(BASE is None or not TOOLS, 'routine base text or the MIPS tools not available')
class Model(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import run_save_stall_probe as bench
        cls.bench = bench
        cls.new = step.firmware_text(BASE)

    def test_cases_pass(self):
        self.assertEqual([(n, d) for n, p, d in self.bench.checks(self.new) if not p], [])

    def test_early_load_bench_still_passes(self):
        self.assertEqual([(n, d) for n, p, d in self.bench.early.checks(self.new) if not p], [])

    def test_faults_caught(self):
        for name in step.FAULTS:
            self.assertTrue([n for n, p, _ in self.bench.checks(step.fault_text(self.new, name)) if not p], name)


if __name__ == '__main__':
    unittest.main()
