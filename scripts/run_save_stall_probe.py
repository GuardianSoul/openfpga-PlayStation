#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Routine-level bench of the EXPERIMENT routine "stall-probe" (scripts/prepare_save_stall_probe.py):
the probe block at stage 2's entry with its count word and table, cut out of the routine text,
assembled with the routine's equates and run on the MIPS I model of run_save_mdec_tap.py, once per
request with the count kept between them (as the SDRAM word is). put_page and answer are stubs
that record page 0 as stored and the FINISH value; the size register is psx_save_exp1's
(size_pages[7:0], [11:8] <= data[3:0]; 2,048 after reset). The page 0 a no-op save stores then
goes through the routine's own load decision (run_save_early_load.py's model).

Usage: run_save_stall_probe.py
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import prepare_save_stall_probe as step
import run_save_early_load as early
from run_save_mdec_tap import Mips, Stop
from tool_paths import find_tool

EXP1, PAGE, BASE = early.EXP1, early.PAGE, 0x80200000
MODEL_SHIFT = 3                     # all requests; the ROM's 17 for the first three as well


def block(text):
    """(code, symbols) of the probe block as the routine has it."""
    equates = ''.join(l + '\n' for l in text.splitlines() if re.match(r'\s+\.equ\s', l))
    probe = early.cut(text, '        # stall-probe (EXPERIMENT)', '95:\n')
    data = early.cut(text, 'probe_n:', '\n', inclusive=True) + text[text.index('        .word   0\nprobe_tab:'):]
    source = ('        .set    noreorder\n        .set    noat\n        .text\n' + equates +
              '        .globl  entry\nentry:\n' + probe + '95:\nreal:\n        break\n'
              'answer:\n        sb      $t0, FINISH($k0)\n        break\n'
              'put_page:\n        jr      $ra\n        sb      $a0, PAGE_LO($k0)\n        .p2align 2\n' + data)
    link = f'SECTIONS {{ .text 0x{BASE:08X} : {{ *(.text) }} /DISCARD/ : {{ *(.MIPS.abiflags) *(.reginfo) *(.comment) }} }}\n'
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'b.S').write_text(source); (tmp / 'b.ld').write_text(link)
        tool = lambda n: find_tool(n) or n
        subprocess.run([tool('llvm-mc'), '-triple=mipsel', '-mcpu=mips1', '-filetype=obj', str(tmp / 'b.S'), '-o', str(tmp / 'b.o')], check=True)
        subprocess.run([tool('ld.lld'), '-m', 'elf32ltsmip', '-T', str(tmp / 'b.ld'), '-e', 'entry', '--no-dynamic-linker', '-o', str(tmp / 'b.elf'), str(tmp / 'b.o')], check=True)
        subprocess.run([tool('llvm-objcopy'), '-O', 'binary', '-j', '.text', str(tmp / 'b.elf'), str(tmp / 'b.bin')], check=True)
        nm = subprocess.run([tool('llvm-nm'), str(tmp / 'b.elf')], check=True, capture_output=True, text=True).stdout
        return (tmp / 'b.bin').read_bytes(), {l.split()[2]: int(l.split()[0], 16) for l in nm.splitlines() if len(l.split()) == 3}


class Device:
    """Stage 2's SDRAM (the block), the staging page with the game's registers, the size register."""
    def __init__(self, code):
        self.ram = bytearray(code) + bytes(8)                     # (SDRAM goes on behind the table)
        self.page = bytearray(b'\xA5' * 0x800)                    # the game's registers; word 0 whatever it was
        self.size_pages = 2048
        self.new_request()

    def new_request(self):
        self.stored, self.finishes, self.size_writes, self.ram_writes = None, [], [], []

    def read(self, address, size):
        if BASE <= address < BASE + len(self.ram): return int.from_bytes(self.ram[address - BASE:address - BASE + size], 'little')
        raise Stop(f'read of {address:08x}')

    def write(self, address, size, value):
        data = (value & (1 << 8 * size) - 1).to_bytes(size, 'little')
        offset = address - EXP1
        if BASE <= address < BASE + len(self.ram): self.ram[address - BASE:address - BASE + size] = data; self.ram_writes.append(address)
        elif PAGE <= offset < PAGE + 0x800: self.page[offset - PAGE:offset - PAGE + size] = data
        elif offset == 0x1801 and size == 1: self.stored = (value, bytes(self.page))      # the put_page stub: page number, contents
        elif offset == 0x1803 and size == 1: self.finishes.append(value)
        elif offset == 0x1818 and size == 1: self.size_pages = self.size_pages & 0xF00 | value; self.size_writes.append('lo')
        elif offset == 0x1819 and size == 1: self.size_pages = self.size_pages & 0x0FF | (value & 0xF) << 8; self.size_writes.append('hi')
        else: raise Stop(f'write of {address:08x}')


def request(code, sym, device, load=False, size_before=None):
    """One request at stage 2's entry: what it did."""
    device.new_request()
    if size_before is not None: device.size_pages = size_before
    m = Mips(code, BASE, device)
    m.r[26], m.r[27] = EXP1, 4 if load else 0                    # k0, k1
    m.run(BASE, 80_000_000)
    count = int.from_bytes(device.ram[sym['probe_n'] - BASE:sym['probe_n'] - BASE + 4], 'little')
    return dict(steps=m.steps, finishes=list(device.finishes), stored=device.stored, size_pages=device.size_pages,
                size_writes=sorted(device.size_writes), count=count, ram_writes=list(device.ram_writes))


def checks(text):
    """list of (name, passed, detail)"""
    try:
        return _checks(text)
    except Stop as stop:
        return [('the model ran', False, dict(stop=str(stop)))]


def _checks(text):
    rows = []
    load = early.build(text)
    plain = None
    for shift, saves in ((MODEL_SHIFT, step.PROBES + 2), (step.PROBE_SHIFT, 3)):
        code, sym = block(text.replace(f'PROBE_SHIFT, {step.PROBE_SHIFT} ', f'PROBE_SHIFT, {shift} '))
        device = Device(code)
        for save in range(1, saves + 1):
            if save == 3:                                         # a load between the saves: nothing of the probe
                got = request(code, sym, device, load=True)
                rows.append((f'shift {shift}: a load between saves goes to the real routine, count kept',
                             got['finishes'] == [] and got['stored'] is None and got['ram_writes'] == [] and got['count'] == 2, dict(count=got['count'])))
            got = request(code, sym, device, size_before=24 if save == 2 else None)
            detail = dict(steps=got['steps'], finishes=got['finishes'], size_pages=got['size_pages'], count=got['count'])
            if save <= step.PROBES:
                if plain is None: plain = got['steps']
                rounds = (got['steps'] - plain) / 2 + 1           # the loop: 2 instructions a round
                page0 = got['stored'][1] if got['stored'] else b''
                detail.update(rounds=rounds, magic=page0[:4].hex(), page=got['stored'] and got['stored'][0])
                ok = (rounds == step.rounds(save, shift) and got['finishes'] == [1] and got['size_pages'] == 2048 and got['size_writes'] == ['hi', 'lo']
                      and got['stored'] and got['stored'][0] == 0 and page0[:4] == bytes(4) and page0[4:] == b'\xA5' * 0x7FC
                      and got['count'] == save and got['ram_writes'] == [sym['probe_n']])
                rows.append((f'shift {shift}: save {save} is a no-op save, {step.rounds(save, shift)} rounds, magic 0, 2,048 pages, FINISH 1', ok, detail))
                if ok and shift == MODEL_SHIFT and save in (1, step.PROBES):
                    mc = early.Machine(0)                         # the load of that Memory: refused before any write
                    mc.page[:] = page0
                    m = early.run(*load[:3], 'block_load', mc)
                    rows.append((f'save {save}\'s Memory does not load (FINISH 2, nothing written)',
                                 m.r[3] == 0x0BAD and mc.finishes == [2] and not mc.disc_set_writes and not mc.commands and not mc.other_writes,
                                 dict(finishes=mc.finishes)))
            else:
                ok = got['finishes'] == [] and got['stored'] is None and got['ram_writes'] == [] and got['count'] == step.PROBES and got['size_writes'] == []
                rows.append((f'shift {shift}: save {save} is the real save (count stays {step.PROBES}, nothing written)', ok, detail))
    return rows


def main():
    new = step.firmware_text(step.base_text())
    ok = True
    for name, passed, detail in checks(new):
        print(f"{'ok  ' if passed else 'FAIL'} {name}: {detail}")
        ok &= passed
    failed = [n for n, p, _ in early.checks(new) if not p]
    print('early-load bench on the routine with the probe: ' + ('pass' if not failed else f'FAIL {failed}'))
    ok &= not failed
    for fault in step.FAULTS:
        failed = [n for n, p, _ in checks(step.fault_text(new, fault)) if not p]
        print(f"fault {fault}: {'caught by ' + str(len(failed)) + ' case(s): ' + failed[0] if failed else 'NOT caught'}")
        ok &= bool(failed)
    print('PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
