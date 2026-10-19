#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Build the save-state handler (rtl/pocket/save_fw) into rtl/pocket/psx_save_fw.hex.

psx_save_exp1's 4 KiB ROM, one byte per line, linked at 0xBF000000 (the
EXP1 window the CPU trap vectors to). The hex is committed, so building the
core needs no MIPS toolchain; run this after changing the handler (llvm-mc,
ld.lld, llvm-objcopy). --check rebuilds into a temporary file and fails if
the committed hex differs.
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from tool_paths import ROOT, find_tool

SRC = ROOT / 'rtl/pocket/save_fw/save_handler.S'
HEX = ROOT / 'rtl/pocket/psx_save_fw.hex'
RAM_PAGES = 1024        # 2 MiB main RAM in 2 KiB pages
VRAM_ROWS = 512         # 1 MiB VRAM, one 1024-pixel row per 2 KiB page
SPU_PAGES = 256         # 512 KiB SPU RAM in 2 KiB pages


def image(source=SRC, ram_pages=RAM_PAGES, vram_rows=VRAM_ROWS, spu_pages=SPU_PAGES, diag=False, noop=False):
    """The 4096-byte ROM image, with words 0xFF4/0xFF8/0xFFC set to spu_pages/vram_rows/ram_pages."""
    tools = [find_tool(t) for t in ('llvm-mc', 'ld.lld', 'llvm-objcopy')]
    if not all(tools):
        raise SystemExit('llvm-mc, ld.lld and llvm-objcopy are needed for the save handler')
    mc, ld, objcopy = tools
    with tempfile.TemporaryDirectory() as tmp:
        obj, elf, binary = Path(tmp) / 'h.o', Path(tmp) / 'h.elf', Path(tmp) / 'h.bin'
        subprocess.run([mc, '-triple=mipsel', '-mcpu=mips1', '-filetype=obj', *(['--defsym=DIAG=1'] if diag else []),
                        *(['--defsym=NOOP=1'] if noop else []),
                        str(source), '-o', str(obj)], check=True)
        subprocess.run([ld, '-m', 'elf32ltsmip', '-Ttext=0xBF000000', '-e', 'entry', '--no-dynamic-linker',
                        '-o', str(elf), str(obj)], check=True)
        subprocess.run([objcopy, '-O', 'binary', '-j', '.text', str(elf), str(binary)], check=True)
        data = bytearray(binary.read_bytes())
    if len(data) != 4096:
        raise SystemExit(f'save handler is {len(data)} bytes, not 4096')
    data[0xFF4:0xFF8] = spu_pages.to_bytes(4, 'little')
    data[0xFF8:0xFFC] = vram_rows.to_bytes(4, 'little')
    data[0xFFC:0x1000] = ram_pages.to_bytes(4, 'little')
    return bytes(data)


def byte_hex(data):
    return ''.join(f'{b:02x}\n' for b in data)


def word_hex(data):
    return ''.join(f'{int.from_bytes(data[i:i + 4], "little"):08x}\n' for i in range(0, len(data), 4))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--check', action='store_true', help='fail if the committed hex is stale')
    parser.add_argument('--diag', action='store_true', help='hardware diagnosis build: step blocks on screen, waits time out (writes psx_save_fw_diag.hex)')
    parser.add_argument('--noop', action='store_true', help='hardware diagnosis build: report a save done at once without copying anything, refuse loads (writes psx_save_fw_noop.hex)')
    args = parser.parse_args()
    if args.noop:
        out = HEX.with_name('psx_save_fw_noop.hex'); out.write_text(byte_hex(image(noop=True)))
        print(f'{out.relative_to(ROOT)} written'); return
    if args.diag:
        out = HEX.with_name('psx_save_fw_diag.hex'); out.write_text(byte_hex(image(diag=True)))
        print(f'{out.relative_to(ROOT)} written'); return
    text = byte_hex(image())
    if args.check:
        if not HEX.exists() or HEX.read_text() != text:
            raise SystemExit('rtl/pocket/psx_save_fw.hex is stale: run scripts/build_save_fw.py')
        print('save handler hex is current')
        return
    HEX.write_text(text)
    print(f'{HEX.relative_to(ROOT)} written')


if __name__ == '__main__':
    sys.exit(main())
