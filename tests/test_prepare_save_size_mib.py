"""prepare_save_size_mib.py: the step applies once, changes only the size report, its planted faults apply,
and pack_end behaves as designed on the MIPS model (scripts/run_save_size_mib.py); the early-load bench
still passes with the step chained."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_save_size_mib as step
from tool_paths import find_tool

try:
    BASE = step.base_text()
except Exception as error:   # the reference checkouts the routine text is built from are not here
    BASE = None
TOOLS = all(find_tool(n) or __import__('shutil').which(n) for n in ('llvm-mc', 'ld.lld', 'llvm-objcopy', 'llvm-nm'))


class Numbers(unittest.TestCase):
    def test_rounding(self):
        self.assertEqual([step.reported_pages(n) for n in (1, 511, 512, 513, 1536, 1801)], [512, 512, 512, 1024, 1536, 2048])
        self.assertEqual(step.MAX_STATE_PAGES, 1801)
        self.assertLessEqual(step.reported_pages(step.MAX_STATE_PAGES), step.BUFFER_PAGES)

    def test_bench_text(self):
        new = step.bench_text('   assert size_pages = stored report "x"')
        self.assertIn('(stored + 511) / 512 * 512', new)
        with self.assertRaises(ValueError):
            step.bench_text(new)


@unittest.skipIf(BASE is None, 'routine base text not available')
class Text(unittest.TestCase):
    def test_applies_once(self):
        new = step.firmware_text(BASE)
        self.assertNotEqual(new, BASE)
        with self.assertRaises(ValueError):
            step.firmware_text(new)

    def test_only_the_size_report_changes(self):
        new = step.firmware_text(BASE).splitlines()
        gone = [l for l in BASE.splitlines() if l not in new]
        self.assertEqual([l.split('#')[0].split() for l in gone], [['sb', '$s7,', 'SIZE_LO($k0)'], ['srl', '$t0,', '$s7,', '8']])
        self.assertEqual('\n'.join(new).count(step.STORE), 1)                # the stored count: as it was
        for line in BASE.splitlines():
            if '.equ' in line:
                self.assertIn(line, new)

    def test_faults_apply(self):
        new = step.firmware_text(BASE)
        for name in step.FAULTS:
            self.assertNotEqual(step.fault_text(new, name), new, name)


@unittest.skipIf(BASE is None or not TOOLS, 'routine base text or the MIPS tools not available')
class Model(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import run_save_size_mib as bench
        cls.bench = bench
        cls.new = step.firmware_text(BASE)

    def test_cases_pass(self):
        self.assertEqual([(n, d) for n, p, d in self.bench.checks(self.new) if not p], [])

    def test_exact_size_routine_differs(self):
        failed = [n for n, p, _ in self.bench.checks(BASE) if not p]
        self.assertTrue(failed)
        self.assertFalse([n for n in failed if 'load accepts' in n])         # its stored count loads all the same

    def test_early_load_bench_still_passes(self):
        self.assertEqual([(n, d) for n, p, d in self.bench.early.checks(self.new) if not p], [])

    def test_faults_caught(self):
        for name in step.FAULTS:
            self.assertTrue([n for n, p, _ in self.bench.checks(step.fault_text(self.new, name)) if not p], name)


if __name__ == '__main__':
    unittest.main()
