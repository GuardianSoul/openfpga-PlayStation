# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""rom-room (docs/save-states-finish/rom-room.md): the lz2 stream, and the changed stub code run in a
small MIPS-I model with R3000 load delays (a load or mfc0 result is seen one instruction late)."""
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import build_staged_save_fw as fw                  # noqa: E402
import prepare_save_rom_room as rr                 # noqa: E402
from tool_paths import find_tool                   # noqa: E402

STAGE2 = 0x80200000


def routine_text():
    """The release chain (routine g + readout-hold), the input of the step."""
    import prepare_save_final_routine as fr
    import prepare_save_readout_hold_routine as rh
    return rh.firmware_text(fr.firmware_text('g'))


def link(text):
    """(stub bytes at 0xBF000000, symbols) of a routine text."""
    mc, ld, objcopy, nm = (find_tool(t) for t in ('llvm-mc', 'ld.lld', 'llvm-objcopy', 'llvm-nm'))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'h.S').write_text(text)
        (tmp / 'h.ld').write_text(fw.LINK)
        subprocess.run([mc, '-triple=mipsel', '-mcpu=mips1', '-filetype=obj', str(tmp / 'h.S'), '-o', str(tmp / 'h.o')], check=True)
        subprocess.run([ld, '-m', 'elf32ltsmip', '-T', str(tmp / 'h.ld'), '-e', 'entry', '--no-dynamic-linker',
                        '-o', str(tmp / 'h.elf'), str(tmp / 'h.o')], check=True)
        subprocess.run([objcopy, '-O', 'binary', '-j', '.text', str(tmp / 'h.elf'), str(tmp / 'stub.bin')], check=True)
        syms = dict((n, int(a, 16)) for a, _, n in (l.split() for l in subprocess.run(
            [nm, str(tmp / 'h.elf')], check=True, capture_output=True, text=True).stdout.splitlines() if len(l.split()) == 3))
        return (tmp / 'stub.bin').read_bytes(), syms


class Mips:
    """MIPS-I subset with branch delay slots and load delays; memory is a dict of bytes."""

    def __init__(self, code, base):
        self.code, self.base = code, base
        self.r, self.mem, self.cop0 = [0] * 32, {}, {}
        self.pending = None                         # (register, value) of the last load / mfc0

    def load_word(self, a):
        return self.word(a)

    def word(self, a):
        return sum(self.mem.get(a + i, 0) << 8 * i for i in range(4))

    def run(self, pc, stop, limit=10_000_000):
        nxt = pc + 4
        for _ in range(limit):
            if pc == stop:
                return
            ins = int.from_bytes(self.code[pc - self.base:pc - self.base + 4], 'little')
            load, self.pending = self.pending, None
            target = self.execute(ins, pc)
            if load:
                self.r[load[0]] = load[1]
            self.r[0] = 0
            pc, nxt = nxt, (target if target is not None else nxt + 4)
        raise TimeoutError('model: no stop')

    def execute(self, i, pc):
        op, rs, rt, rd, sa, fn = i >> 26, i >> 21 & 31, i >> 16 & 31, i >> 11 & 31, i >> 6 & 31, i & 63
        imm, r = i & 0xFFFF, self.r
        simm = imm - 0x10000 if imm & 0x8000 else imm
        addr = (r[rs] + simm) & 0xFFFFFFFF
        branch = (pc + 4 + (simm << 2)) & 0xFFFFFFFF
        if op == 0:
            if fn == 0:
                r[rd] = r[rt] << sa & 0xFFFFFFFF
            elif fn == 2:
                r[rd] = r[rt] >> sa
            elif fn == 33:
                r[rd] = (r[rs] + r[rt]) & 0xFFFFFFFF
            elif fn == 35:
                r[rd] = (r[rs] - r[rt]) & 0xFFFFFFFF
            elif fn == 37:
                r[rd] = r[rs] | r[rt]
            elif fn == 36:
                r[rd] = r[rs] & r[rt]
            elif fn == 8:
                return r[rs]
            else:
                raise ValueError(f'model: no such instruction {i:08x}')
        elif op == 4:
            return branch if r[rs] == r[rt] else None
        elif op == 5:
            return branch if r[rs] != r[rt] else None
        elif op == 9:
            r[rt] = addr if rs else simm & 0xFFFFFFFF
        elif op == 12:
            r[rt] = r[rs] & imm
        elif op == 13:
            r[rt] = r[rs] | imm
        elif op == 14:
            r[rt] = r[rs] ^ imm
        elif op == 15:
            r[rt] = imm << 16
        elif op == 16 and rs == 0:
            self.pending = (rt, self.cop0.get(rd, 0))
        elif op == 16 and rs == 4:
            self.cop0[rd] = r[rt]
        elif op == 35:
            self.pending = (rt, self.load_word(addr))
        elif op == 36:
            self.pending = (rt, self.mem.get(addr, 0))
        elif op == 40:
            self.mem[addr] = r[rt] & 0xFF
        elif op == 43:
            for k in range(4):
                self.mem[addr + k] = r[rt] >> 8 * k & 0xFF
        else:
            raise ValueError(f'model: no such instruction {i:08x}')
        return None


def run_unpack(text, stage):
    """The stub's lz2 unpacker from its label to the next label after it, on fw's stream of stage."""
    end = text.index('lz2:')
    end = text.index('\n5:\n', end) + 4
    stub, syms = link(text[:end] + 'cz_u1:\n' + text[end:])
    blob = stage[0:4] + len(stage).to_bytes(4, 'little') + fw.lz2_pack(stage)
    cpu = Mips(stub, 0xBF000000)
    src = 0x80208000
    for k, b in enumerate(blob):
        cpu.mem[src + k] = b
    cpu.r[4] = src                                  # a0: the copied stream
    try:
        cpu.run(syms['lz2'], syms['cz_u1'], limit=40 * len(stage) + 1000)
    except TimeoutError:                            # runs past the end (never meets it): no output
        return None, cpu
    return bytes(cpu.mem.get(STAGE2 + k, 0) for k in range(len(stage))), cpu


class RomRoomTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = routine_text()
        cls.stepped = rr.firmware_text(cls.text)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 's.S'
            path.write_text(cls.stepped)
            cls.image, cls.stage, cls.ext = fw.image_parts_ext(source=path)

    def test_lz2_round_trip(self):
        rng = random.Random(5)
        for n in (0, 4, 8, 64, 400):
            data = bytes(rng.choice(b'\0\1\2AB') for _ in range(n))
            self.assertEqual(fw.lz2_unpack(fw.lz2_pack(data), n), data)
        self.assertEqual(fw.lz2_unpack(fw.lz2_pack(self.stage), len(self.stage)), self.stage)

    def test_lz2_is_smaller_than_lz(self):
        self.assertLess(len(fw.lz2_pack(self.stage)) + 120, len(fw.lz_pack(self.stage)))

    def test_builder_uses_lz2_with_the_label_only(self):
        blob = self.image[972:0xFF4] + self.image[0x1C00:0x2000] + self.ext
        self.assertEqual(blob[8:8 + 64], fw.lz2_pack(self.stage)[:64])
        self.assertIn('lz2:', self.stepped)
        self.assertNotIn('lz2:', self.text)

    def test_stub_unpacks_the_stream(self):
        out, cpu = run_unpack(self.stepped, self.stage)
        self.assertEqual(out, self.stage)

    def test_stub_unpacks_random_data(self):
        rng = random.Random(7)
        data = bytes(rng.choice(b'\0\1\2\3ABCD') for _ in range(2048))
        self.assertEqual(run_unpack(self.stepped, data)[0], data)

    def test_cop0_save_and_restore(self):
        values = {n: 0x1000 * n + 0x11 * n for n in (3, 5, 7, 9, 11)}
        self.assertEqual(cop0_round_text(self.stepped, values), values)

    def test_unpack_faults_are_caught(self):
        for name in ('length-3', 'literal-unswapped', 'source-unswapped'):
            with self.subTest(name):
                out = run_unpack(rr.fault_text(self.stepped, name), self.stage)[0]
                self.assertNotEqual(out, self.stage)

    def test_cop0_faults_are_caught(self):
        values = {n: 0x1000 * n + 0x11 * n for n in (3, 5, 7, 9, 11)}
        for name in ('restore-9-from-11', 'save-7-from-5'):
            with self.subTest(name):
                self.assertNotEqual(cop0_round_text(rr.fault_text(self.stepped, name), values), values)

    def test_nop_delay_fault_is_caught(self):
        """The model's load delay is real: a block with a load used in its delay slot fails."""
        bad = self.stepped.replace('        mfc0    $t3, $11\n        sw      $t4, PAGE+4*46($k0)\n        sw      $t3, PAGE+4*47($k0)\n',
                                   '        mfc0    $t3, $11\n        sw      $t3, PAGE+4*47($k0)\n        sw      $t4, PAGE+4*46($k0)\n')
        self.assertNotEqual(bad, self.stepped)
        values = {n: 0x1000 * n + 0x11 * n for n in (3, 5, 7, 9, 11)}
        self.assertNotEqual(cop0_round_text(bad, values), values)


def cop0_round_text(text, values):
    """Run the entry's COP0 save block, then restore's COP0 load block; -> the COP0 registers written. Markers go by position (the entry's block starts
    at its first mfc0 $t3, $3 and ends after the last sw to PAGE+4*47; the restore's from its first lw of
    PAGE+4*43 to its mtc0 $t0, $11)."""
    lines = text.split('\n')
    def find(pred, start=0):
        return next(k for k in range(start, len(lines)) if pred(lines[k]))
    e0 = find(lambda l: l.strip().startswith('mfc0    $t3, $3'))
    e1 = find(lambda l: 'PAGE+4*47($k0)' in l and l.strip().startswith('sw'), e0) + 1
    e1 = max(e1, find(lambda l: 'PAGE+4*46($k0)' in l and l.strip().startswith('sw'), e0) + 1)
    r0 = find(lambda l: l.strip().startswith('lw      $t0, PAGE+4*43($k0)'))
    r1 = find(lambda l: l.strip().startswith('mtc0    $t0, $11'), r0) + 1
    for at, name in sorted(((e0, 'cz_e0'), (e1, 'cz_e1'), (r0, 'cz_r0'), (r1, 'cz_r1')), reverse=True):
        lines.insert(at, name + ':')
    stub, syms = link('\n'.join(lines))
    cpu = Mips(stub, 0xBF000000)
    cpu.r[26] = 0xBF000000
    cpu.cop0.update(values)
    cpu.run(syms['cz_e0'], syms['cz_e1'])
    cpu.cop0 = {}
    cpu.run(syms['cz_r0'], syms['cz_r1'])
    return cpu.cop0


if __name__ == '__main__':
    unittest.main()
