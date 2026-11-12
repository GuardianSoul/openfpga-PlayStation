#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Routine-level bench of the size-mib step (scripts/prepare_save_size_mib.py): the routine's own
pack_end, cut out of the routine text, assembled with its equates and run on the MIPS I model of
run_save_mdec_tap.py with s7 = the true stored page count. get_page / put_page / copy_in are stubs;
the model takes page 0 as put_page stored it and the SIZE_LO / SIZE_HI writes the way
psx_save_exp1 does (size_pages[7:0], size_pages[11:8] <= data[3:0]; savestate_size = pages << 11).
The stored word 38 then goes through the routine's own load decision (run_save_early_load.py's
model, the page-count check against the bitmap) for a layout of that many pages.

Usage: run_save_size_mib.py
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import prepare_save_size_mib as step
import run_save_early_load as early
from run_save_mdec_tap import Mips, Stop
from tool_paths import find_tool

EXP1, PAGE, BASE = early.EXP1, early.PAGE, 0x80200000
COUNTS = {1: 512, 511: 512, 512: 512, 513: 1024, 1536: 1536, 1801: 2048}


def block(text):
    equates = ''.join(l + '\n' for l in text.splitlines() if re.match(r'\s+\.equ\s', l))
    pack_end = early.cut(text, 'pack_end:\n', '# save: v0 = 0 at a CD quiet point')
    source = ('        .set    noreorder\n        .set    noat\n        .text\n' + equates +
              '        .globl  entry\nentry:\n        jal     pack_end\n        nop\n        break\n' + pack_end +
              'get_page:\ncopy_in:\n        jr      $ra\n        nop\n'
              'put_page:\n        jr      $ra\n        sb      $a0, PAGE_LO($k0)\n')
    link = f'SECTIONS {{ .text 0x{BASE:08X} : {{ *(.text) }} /DISCARD/ : {{ *(.MIPS.abiflags) *(.reginfo) *(.comment) }} }}\n'
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'b.S').write_text(source); (tmp / 'b.ld').write_text(link)
        tool = lambda n: find_tool(n) or n
        subprocess.run([tool('llvm-mc'), '-triple=mipsel', '-mcpu=mips1', '-filetype=obj', str(tmp / 'b.S'), '-o', str(tmp / 'b.o')], check=True)
        subprocess.run([tool('ld.lld'), '-m', 'elf32ltsmip', '-T', str(tmp / 'b.ld'), '-e', 'entry', '--no-dynamic-linker', '-o', str(tmp / 'b.elf'), str(tmp / 'b.o')], check=True)
        subprocess.run([tool('llvm-objcopy'), '-O', 'binary', '-j', '.text', str(tmp / 'b.elf'), str(tmp / 'b.bin')], check=True)
        return (tmp / 'b.bin').read_bytes()


class Device:
    """The staging page, the scratchpad (bitmap: no page left out) and psx_save_exp1's size register."""
    def __init__(self):
        self.page, self.stored_page0, self.size_pages, self.size_writes = bytearray(0x800), None, 2048, []

    def read(self, address, size):
        if 0x1F800000 <= address < 0x1F800400: return 0
        offset = address - EXP1
        if PAGE <= offset < PAGE + 0x800: return int.from_bytes(self.page[offset - PAGE:offset - PAGE + size], 'little')
        raise Stop(f'read of {address:08x}')

    def write(self, address, size, value):
        offset = address - EXP1
        if PAGE <= offset < PAGE + 0x800: self.page[offset - PAGE:offset - PAGE + size] = (value & (1 << 8 * size) - 1).to_bytes(size, 'little')
        elif offset == 0x1801 and size == 1: self.stored_page0 = bytes(self.page)          # the put_page stub
        elif offset == 0x1818 and size == 1: self.size_pages = self.size_pages & 0xF00 | value & 0xFF; self.size_writes.append('lo')
        elif offset == 0x1819 and size == 1: self.size_pages = self.size_pages & 0x0FF | (value & 0xF) << 8; self.size_writes.append('hi')
        else: raise Stop(f'write of {address:08x}')


def save_end(code, stored):
    """(pages reported, bytes the Pocket copies, word 38 as stored) for a save of `stored` pages."""
    device = Device()
    m = Mips(code, BASE, device)
    m.r[26], m.r[23] = EXP1, stored                              # k0, s7
    m.run(BASE, 100_000)
    if device.stored_page0 is None or sorted(device.size_writes) != ['hi', 'lo']:
        raise Stop('page 0 not stored, or not one write of each size byte')
    return device.size_pages, device.size_pages << 11, int.from_bytes(device.stored_page0[4 * 38:4 * 38 + 4], 'little')


def checks(text):
    """list of (name, passed, detail)"""
    code = block(text)
    load = early.build(text)
    rows = []
    for stored, want in COUNTS.items():
        pages, size, word = save_end(code, stored)
        rows.append((f'{stored} stored pages: {want} reported, word 38 = {stored}',
                     pages == want and size == want * 2048 and word == stored and pages <= step.BUFFER_PAGES, dict(reported=pages, bytes=size, word38=word)))
        if stored >= 9:                                           # a layout of that many pages: the routine's load takes the state
            n = min(stored - 9, 1024); r = min(stored - 9 - n, 512); s = stored - 9 - n - r
            mc = early.Machine(load[3], pages=(n, r, s))
            mc.page[4 * 38:4 * 38 + 4] = word.to_bytes(4, 'little')
            m = early.run(*load[:3], 'block_load', mc)
            rows.append((f'{stored} stored pages: the load accepts the stored count', m.r[3] != 0x0BAD and mc.finishes != [2], dict(word38=word, finishes=mc.finishes)))
    worst = max(save_end(code, n)[0] for n in range(1, step.MAX_STATE_PAGES + 1))
    exact = all(save_end(code, n)[0] == step.reported_pages(n) for n in range(1, step.MAX_STATE_PAGES + 1))
    rows.append((f'every count 1..{step.MAX_STATE_PAGES}: whole MiB, at most {step.BUFFER_PAGES} pages', exact and worst == step.BUFFER_PAGES, dict(largest=worst)))
    return rows


def main():
    base = step.base_text()
    new = step.firmware_text(base)
    ok = True
    for name, passed, detail in checks(new):
        print(f"{'ok  ' if passed else 'FAIL'} {name}: {detail}")
        ok &= passed
    for name, passed, detail in early.checks(new):               # the early-load bench on the chained routine
        if not passed: print(f'FAIL early-load bench: {name}: {detail}')
        ok &= passed
    print('early-load bench on the chained routine: ' + ('pass' if ok else 'see above'))
    exact = [n for n, p, _ in checks(base) if not p]
    print(f'routine c (exact size) fails {len(exact)} of the size cases (expected: it reports the exact count)')
    ok &= bool(exact)
    for fault in step.FAULTS:
        failed = [n for n, p, _ in checks(step.fault_text(new, fault)) if not p]
        print(f"fault {fault}: {'caught by ' + str(len(failed)) + ' case(s): ' + failed[0] if failed else 'NOT caught'}")
        ok &= bool(failed)
    print('PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
