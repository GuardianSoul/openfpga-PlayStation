#!/usr/bin/env python3
"""Build psx_cue_mount's firmware (rtl/pocket/cue_fw) into rtl/pocket/psx_cue_fw0.hex .. 3.hex.

The hex files (one per byte lane of the RAM) are committed, so building the core needs no RISC-V toolchain; run
this after changing the firmware (clang with the riscv32 target, ld.lld and
llvm-objcopy). --check rebuilds into a temporary file and fails if the
committed hex differs.
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from tool_paths import ROOT, find_tool

SRC = ROOT/'rtl/pocket/cue_fw'
HEX = [ROOT/f'rtl/pocket/psx_cue_fw{lane}.hex' for lane in range(4)]
WORDS = 4096        # psx_cue_mount's RAM
CODE_LIMIT = 0x3E00  # link.ld: the stack starts here


def build(out):
    clang, objcopy = find_tool('clang'), find_tool('llvm-objcopy')
    if not clang or not objcopy: raise SystemExit('clang and llvm-objcopy are needed')
    with tempfile.TemporaryDirectory() as tmp:
        elf, binary = Path(tmp)/'fw.elf', Path(tmp)/'fw.bin'
        subprocess.run([clang, '--target=riscv32-unknown-elf', '-march=rv32i', '-mabi=ilp32', '-Os',
                        '-ffreestanding', '-fno-builtin', '-nostdlib', '-mno-relax', '-msmall-data-limit=0',
                        '-fuse-ld=lld', '-Wall', '-Werror', '-Wl,-T,' + str(SRC/'link.ld'), '-Wl,--gc-sections',
                        '-ffunction-sections', '-o', str(elf), str(SRC/'start.S'), str(SRC/'cue_mount.c')], check=True)
        subprocess.run([objcopy, '-O', 'binary', '-j', '.text', '-j', '.high', str(elf), str(binary)], check=True)
        data = binary.read_bytes()
    if len(data) > CODE_LIMIT: raise SystemExit(f'firmware is {len(data)} bytes, over {CODE_LIMIT}')
    data += bytes(WORDS*4 - len(data))
    for lane, path in enumerate(out): path.write_text(''.join(f'{b:02x}\n' for b in data[lane::4]))
    return len(data.rstrip(b'\0'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='fail if the committed hex is stale')
    args = parser.parse_args()
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            out = [Path(tmp)/f'fw{lane}.hex' for lane in range(4)]
            build(out)
            if any(a.read_text() != b.read_text() for a, b in zip(out, HEX)): raise SystemExit('rtl/pocket/psx_cue_fw*.hex are stale: run scripts/build_cue_fw.py')
        print('firmware hex is current')
        return
    size = build(HEX)
    print(f'rtl/pocket/psx_cue_fw0-3.hex: image ends at {size:#x} (stack from {CODE_LIMIT:#x})')


if __name__ == '__main__':
    sys.exit(main())
