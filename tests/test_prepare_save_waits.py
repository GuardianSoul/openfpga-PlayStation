"""prepare_save_waits.py and prepare_save_final_routine.py: the parts apply alone and together on routine
c2, every wait of the final routines has a bound, the planted faults apply, and the two final routines
assemble into their ROMs with the sizes the hand-over reports."""
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_save_waits as step
import prepare_save_final_routine as final

try:
    import prepare_save_early_load as early
    C2 = early.firmware_text(early.base_text(), early.PARTS, early.READY_ROUNDS)
except Exception:   # the reference checkouts the routine text is built from are not here
    C2 = None


def loops(text):
    """(label line, body) of every backward branch to a numeric or named label inside stage 2."""
    lines = text[text.index('stage2:'):].splitlines()
    found = []
    for i, line in enumerate(lines):
        m = re.match(r'\s+(b\w*)\s+.*?(\d+)b\b', line.split('#')[0])
        if m:
            start = max(j for j in range(i) if re.match(rf'{m[2]}:', lines[j]))
            found.append((lines[start], lines[start:i + 2]))
    return found


@unittest.skipIf(C2 is None, 'routine base text not available')
class Waits(unittest.TestCase):
    def test_applies_once(self):
        new = step.firmware_text(C2)
        with self.assertRaises(ValueError):
            step.firmware_text(new)

    def test_each_part_alone(self):
        needs = {'scratchpad': ('page',), 'cd-drain': ('cd-count',)}
        for part in step.PARTS:
            text = step.firmware_text(C2, (part,) + needs.get(part, ()))
            self.assertNotEqual(text, C2, part)
        with self.assertRaises(ValueError):
            step.firmware_text(C2, ('scratchpad',))
        with self.assertRaises(ValueError):
            step.firmware_text(C2, ('cd-drain',))
        with self.assertRaises(ValueError):
            step.firmware_text(C2, ('nothing',))

    def test_no_register_of_compress_is_used(self):
        # compress uses fp, gp, sp and at as scratch; the dead-wait flag is k1 bit 1
        added = [l for l in step.firmware_text(C2).splitlines() if l not in C2.splitlines()]
        code = '\n'.join(l.split('#')[0] for l in added)
        for reg in ('$fp', '$gp', '$sp', '$at'):
            self.assertNotIn(reg, code)
        self.assertIn('ori     $k1, $k1, 2', code)

    def test_polls_of_a_device_have_a_bound(self):
        # every loop of stage 2 that reads a device register in its body counts something down or
        # compares against an end: no loop is left that only waits for a bit
        for variant in final.VARIANTS:
            text = final.firmware_text(variant)
            for label, body in loops(text):
                code = [l.split('#')[0] for l in body]
                reads_device = any(re.search(r'lbu?\s+\$\w+, (STATUS|SS_CMD|SPU_HOLD|DISC|DISC_SET|CD_STATE\+7|0x180[0-3])\(', l) or
                                       re.search(r'l[bw]u?\s+\$\w+, 0x18(0[0-3]|24)\(\$t9\)', l) for l in code)
                if not reads_device:
                    continue
                counted = any(re.search(r'addiu\s+(\$\w+), \1, -1', l) for l in code) or any('beq ' in l or 'bne ' in l for l in code)
                self.assertTrue(counted, f'{variant}: unbounded device poll at {label!r}')

    def test_bench_bound_keeps_the_length(self):
        text = step.firmware_text(C2)
        bench = step.bench_bound(text)
        self.assertEqual(len(text.splitlines()), len(bench.splitlines()))
        self.assertEqual(bench.count('BENCH-ONLY OVERRIDE'), 3)

    def test_faults_apply(self):
        text = step.firmware_text(C2)
        for name in step.FAULTS:
            self.assertNotEqual(step.fault_text(text, name), text, name)
            self.assertIn(step.FAULT_PART[name], step.PARTS)


@unittest.skipIf(C2 is None, 'routine base text not available')
class Final(unittest.TestCase):
    def test_chains(self):
        self.assertEqual(final.CHAIN['g'], ('big-build', 'mdec-tap', 'early-load', 'disc-id', 'waits', 'rom-ext', 'compress'))
        self.assertEqual(final.CHAIN['f'], ('big-build', 'mdec-tap', 'early-load', 'waits'))
        for variant in final.VARIANTS:
            text = final.firmware_text(variant)
            self.assertNotIn('size-mib', text)
            self.assertNotIn('addiu   $t0, $s7, 511', text)       # the whole-MiB size report
            self.assertEqual('cz_save' in text, variant == 'g')
            self.assertEqual('disc-id' in text, variant == 'g')
            for part in ('page', 'ss', 'quiet-poll', 'cd-count', 'cd-drain', 'retry', 'fifo-clamp'):
                self.assertIn(part, final.waits_parts(variant))

    def test_g_reports_the_stream_pages(self):
        text = final.firmware_text('g')
        self.assertIn('        sb      $v1, SIZE_LO($k0)       # compress: the pages the stream took, not the stored pages\n', text)

    def test_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = final.build('f', Path(tmp) / 'f')
            g = final.build('g', Path(tmp) / 'g')
            self.assertEqual(f['stub_bytes'], 980)
            self.assertEqual(g['stub_bytes'], 1004)
            self.assertGreaterEqual(f['free_bytes'], 0)
            self.assertGreaterEqual(g['free_bytes'], 40)          # room kept for the step chained after compress
            self.assertTrue((Path(tmp) / 'g' / 'psx_save_rom_ext.hex').exists())
            self.assertEqual(len((Path(tmp) / 'g' / 'psx_save_rom_ext.hex').read_text().split()), 1024)
            self.assertEqual(f['steps'][2]['source_sha256'], g['steps'][2]['source_sha256'])   # both from routine c2
            for report in (f, g):
                self.assertTrue(all(report['faults_assembled'].values()))


if __name__ == '__main__':
    unittest.main()
