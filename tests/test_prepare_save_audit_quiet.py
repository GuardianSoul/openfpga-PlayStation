# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""audit-quiet (docs/save-states-finish/audit-fixes.md): the GPU quiet poll and mdec_quiet of the linked
routine, run in the MIPS-I model of test_prepare_save_rom_room against scripted device registers."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))

import prepare_save_audit_quiet as aq                     # noqa: E402
from test_prepare_save_rom_room import Mips               # noqa: E402

RA = 0x0BADC0DE
BIT26, BIT28 = 1 << 26, 1 << 28


def routine_text():
    """The release chain's routine: the big build's quiet point and R1's mdec_quiet are in every chain."""
    import prepare_save_final_routine as fr
    return fr.firmware_text('g')


class Device(Mips):
    """GPUSTAT reads walk a list (the last value repeats); MDEC status is a fixed value; DMA idle."""

    def __init__(self, code, base, gpustat=(), mdec=0x80000000):
        super().__init__(code, base)
        self.gpustat, self.mdec, self.gpu_reads = list(gpustat), mdec, 0

    def load_word(self, a):
        if a == 0x1F801814:
            self.gpu_reads += 1
            return self.gpustat.pop(0) if len(self.gpustat) > 1 else self.gpustat[0]
        if a == 0x1F801824:
            return self.mdec
        return self.word(a)


def marked(text):
    """text with labels around the GPU/DMA quiet poll (aq_q0 .. aq_q1) and at retry's address."""
    start = text.index('\nquiet:\n')
    end = text.index('        bne     $t2, $t4, 5b\n        nop\n', start) + len('        bne     $t2, $t4, 5b\n        nop\n')
    return text[:end] + 'aq_q1:\n' + text[end:]


class AuditQuietTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = routine_text()
        cls.text = aq.firmware_text(cls.base)

    def stage2(self, text):
        """(stage 2 bytes, symbols) of a text with the poll's end marked."""
        import subprocess
        import tempfile
        import build_staged_save_fw as fw
        from tool_paths import find_tool
        mc, ld, objcopy, nm = (find_tool(t) for t in ('llvm-mc', 'ld.lld', 'llvm-objcopy', 'llvm-nm'))
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'h.S').write_text(marked(text))
            (tmp / 'h.ld').write_text(fw.LINK)
            subprocess.run([mc, '-triple=mipsel', '-mcpu=mips1', '-filetype=obj', str(tmp / 'h.S'), '-o', str(tmp / 'h.o')], check=True)
            subprocess.run([ld, '-m', 'elf32ltsmip', '-T', str(tmp / 'h.ld'), '-e', 'entry', '--no-dynamic-linker',
                            '-o', str(tmp / 'h.elf'), str(tmp / 'h.o')], check=True)
            subprocess.run([objcopy, '-O', 'binary', '-j', '.stage2', str(tmp / 'h.elf'), str(tmp / 's2.bin')], check=True)
            syms = dict((n, int(a, 16)) for a, _, n in (l.split() for l in subprocess.run(
                [nm, str(tmp / 'h.elf')], check=True, capture_output=True, text=True).stdout.splitlines() if len(l.split()) == 3))
            return (tmp / 's2.bin').read_bytes(), syms

    def poll(self, text, gpustat):
        code, syms = self.stage2(text)
        cpu = Device(code, 0x80200000, gpustat)
        pc, stops = syms['quiet'], {syms['aq_q1']: 'quiet', syms['retry']: 'retry'}
        nxt = pc + 4
        for _ in range(200_000):
            if pc in stops:
                return stops[pc], cpu.gpu_reads
            ins = int.from_bytes(code[pc - 0x80200000:pc - 0x80200000 + 4], 'little')
            load, cpu.pending = cpu.pending, None
            target = cpu.execute(ins, pc)
            if load:
                cpu.r[load[0]] = load[1]
            cpu.r[0] = 0
            pc, nxt = nxt, (target if target is not None else nxt + 4)
        raise AssertionError('poll: no end')

    def mdec(self, text, status, first_half):
        code, syms = self.stage2(text)
        cpu = Device(code, 0x80200000, (BIT26 | BIT28,), status)
        cpu.r[10] = 1 if first_half else 0          # t2: sltiu of the retry count (first half = 1)
        cpu.r[31] = RA
        pc, nxt = syms['mdec_quiet'], syms['mdec_quiet'] + 4
        for _ in range(200_000):
            if pc == RA:
                return cpu.r[2]
            ins = int.from_bytes(code[pc - 0x80200000:pc - 0x80200000 + 4], 'little')
            load, cpu.pending = cpu.pending, None
            target = cpu.execute(ins, pc)
            if load:
                cpu.r[load[0]] = load[1]
            cpu.r[0] = 0
            pc, nxt = nxt, (target if target is not None else nxt + 4)
        raise AssertionError('mdec_quiet: no return')

    # GPU (finding 7)
    def test_gpu_waits_for_the_fifo(self):
        self.assertEqual(self.poll(self.text, [BIT26] * 5 + [BIT26 | BIT28]), ('quiet', 6))

    def test_gpu_waits_for_ready(self):
        self.assertEqual(self.poll(self.text, [BIT28] * 3 + [BIT26 | BIT28]), ('quiet', 4))

    def test_gpu_never_quiet_is_a_retry(self):
        self.assertEqual(self.poll(self.text, [BIT26])[0], 'retry')

    def test_gpu_before_the_fix_takes_a_full_fifo(self):
        """The audit's finding on the unchanged text: bit 26 alone passes with words queued."""
        self.assertEqual(self.poll(self.base, [BIT26] * 5 + [BIT26 | BIT28]), ('quiet', 1))

    def test_gpu_faults_are_caught(self):
        for name, seq in (('gpu-bit26-only', [BIT26] * 5 + [BIT26 | BIT28]), ('gpu-bit28-only', [BIT28] * 3 + [BIT26 | BIT28])):
            with self.subTest(name):
                self.assertNotEqual(self.poll(aq.fault_text(self.text, name), seq), ('quiet', len(seq)))

    # MDEC (finding 6)
    def test_mdec_busy_is_a_retry_in_both_halves(self):
        for first_half in (True, False):
            self.assertNotEqual(self.mdec(self.text, 0x20000000, first_half), 0)

    def test_mdec_idle_passes(self):
        for first_half in (True, False):
            self.assertEqual(self.mdec(self.text, 0x80000000, first_half), 0)

    def test_mdec_before_the_fix_passes_late(self):
        self.assertEqual(self.mdec(self.base, 0x20000000, False), 0)

    def test_mdec_fault_is_caught(self):
        self.assertEqual(self.mdec(aq.fault_text(self.text, 'mdec-first-half'), 0x20000000, False), 0)

    def test_parts_alone_and_together_apply(self):
        for parts in (('gpu-fifo',), ('mdec-all',), aq.PARTS):
            aq.firmware_text(self.base, parts)


if __name__ == '__main__':
    unittest.main()
