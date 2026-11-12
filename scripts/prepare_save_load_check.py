#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "load-check": a load refuses a state whose stored-page count does
not match its page bitmap, before anything of the running game is changed.

Design note and bench results: docs/save-states-finish/stage2-mark.md (second part).
A save stores, from page 2 on, only the main RAM pages and VRAM rows that are not all
zero and sets bit p of the page bitmap (page 0 words 64-127) for a logical page p it left
out; page 0 word 38 is the number of stored pages (also the size the Pocket copies).
Logical pages of the routine's layout (the "Save buffer pages" comment, pack_end):
  page 0, page 1, N main RAM pages, R VRAM rows, the SPU register page, 4 decoded-sample
  pages, S SPU RAM pages, 2 CD FIFO pages                      = N + R + S + 9
with N, R, S the ROM words 0xFFC, 0xFF8, 0xFF4 (s2 - RAM_PAGE, s3, s4 at `load`). Only RAM
pages and VRAM rows can be left out, so a state of this routine has
  word 38 = N + R + S + 9 - (bits set in the 64 bitmap words).
The check counts all 2,048 bits: a bit set outside the RAM / VRAM range is refused too.
A mismatch goes to the existing error exit `bad` (FINISH 2, the game carries on). The
check runs with page 0 in the staging page, after the magic compare and (R1) before the
remount; before it only `put_page SCRATCH` and `get_page 0` have run.
Only stage 2 changes (18 instructions, 72 bytes unpacked).
The device's size register (SIZE_LO / SIZE_HI, 0x1818 / 0x1819) is write-only (a read
there gives the CD state), and the routine has no other way to learn how many bytes the
Pocket wrote into the buffer: word 38 cannot be compared with the upload's size.
Usage: prepare_save_load_check.py --output OUT [--base pxs6|r1] [--with-mark]
"""
import argparse
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'load-check anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


CHECK = '''\
        # load-check (scripts/prepare_save_load_check.py): word 38 (stored pages) against the
        # page bitmap, with page 0 in the staging page and nothing of the game changed yet.
        # Expected: N + R + S + 9 logical pages (s2 = N + 2, s3 = R, s4 = S) less the bits set.
        addu    $t3, $s2, $s3
        addu    $t3, $t3, $s4
        addiu   $t3, $t3, 7
        addiu   $t2, $k0, PAGE+0x100    # bitmap: words 64-127
        addiu   $t4, $k0, PAGE+0x200
30:     lw      $t0, 0($t2)
        addiu   $t2, $t2, 4
31:     beqz    $t0, 32f
        addiu   $t1, $t0, -1
        and     $t0, $t0, $t1           # the lowest set bit off
        b       31b
        addiu   $t3, $t3, -1
32:     bne     $t2, $t4, 30b
        nop
        lw      $t0, PAGE+4*38($k0)
        nop
        bne     $t0, $t3, bad           # not a whole state of this layout
        nop
'''

# PXS6 text: after the magic compare
PXS6_OLD = '''\
        bne     $t0, $t1, bad
        nop
        jal     load_start              # page bitmap into the scratchpad, drive paused and set up
'''
PXS6_NEW = '''\
        bne     $t0, $t1, bad
        nop
''' + CHECK + '''\
        jal     load_start              # page bitmap into the scratchpad, drive paused and set up
'''
# R1 text: after the magic compare (its delay slot reads the menu's disc), before the remount
R1_OLD = '''\
        beqz    $t0, bad
        lbu     $s5, DISC($k0)          # the menu's disc now: put back if the state's does not mount
'''
R1_NEW = R1_OLD + CHECK

# Deliberate faults for the bench (never a fitted input)
FAULTS = {
    # the count is only an upper bound: a state with a smaller word 38 (a truncated one) passes
    'check-accepts-smaller': ('        bne     $t0, $t3, bad           # not a whole state of this layout\n',
                              '        sltu    $t0, $t3, $t0           # FAULT: only "not more than expected"\n'
                              '        bnez    $t0, bad\n'),
    # the bitmap is not counted: the expected count is the constant N + R + S + 9
    'check-ignores-bitmap': ('        b       31b\n        addiu   $t3, $t3, -1\n',
                             '        b       31b\n        nop                             # FAULT: bits not counted\n'),
}


def firmware_text(text):
    """text: the PXS6 or the R1 routine, with or without stage2-mark -> the same with the check."""
    if 'load-check' in text:
        raise ValueError('load-check is applied already')
    import re
    for label in ('30', '31', '32'):
        if re.search(rf'^{label}:|[ ,]{label}[fb]\b', text, flags=re.M):
            raise ValueError(f'numeric label {label} is in use')
    if 'MAGIC_R1' in text:
        return once(text, R1_OLD, R1_NEW)
    return once(text, PXS6_OLD, PXS6_NEW)


def fault_text(text, name):
    old, new = FAULTS[name]
    return once(text, old, new)


def build(output_dir, base='pxs6', with_mark=False):
    from build_staged_save_fw import image_parts, rom1, rom2, rom40_hex, free_bytes
    from prepare_save_r1 import stub_bytes, packed_bytes
    import prepare_save_stage2_mark as mark
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    text = mark.base_text(base)
    name = base
    if with_mark:
        text, name = mark.firmware_text(text), base + '-mark'
    old_source = output_dir / f'staged-{name}.S'
    old_source.write_text(text)
    source = output_dir / f'staged-{name}-check.S'
    source.write_text(firmware_text(text))
    old_image, old_stage = image_parts(source=old_source)
    image, stage = image_parts(source=source)           # stops if the packed stage 2 does not fit
    old_stub, stub = stub_bytes(old_source), stub_bytes(source)
    if old_stub != stub:
        raise ValueError('load-check changed the stub: only stage 2 may change')
    for fault in FAULTS:
        faulty = output_dir / f'staged-{name}-check-fault-{fault}.S'
        faulty.write_text(fault_text(source.read_text(), fault))
        image_parts(source=faulty)
    rom = output_dir / 'psx_save_rom40.hex'
    rom.write_text(rom40_hex(rom1(image), rom2(image)))
    report = dict(base=name, source=str(source), rom=str(rom),
                  source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  rom_sha256=hashlib.sha256(rom.read_bytes()).hexdigest(),
                  stub_bytes=len(stub), stub_unchanged=True,
                  stage2_bytes_before=len(old_stage), stage2_bytes=len(stage),
                  packed_bytes_before=packed_bytes(old_image, len(old_stub)), packed_bytes=packed_bytes(image, len(stub)),
                  free_bytes_before=free_bytes(old_image), free_bytes=free_bytes(image))
    print(f"load-check on {name}: {source}")
    print(f"stub:             {report['stub_bytes']} bytes, unchanged")
    print(f"unpacked stage 2: {report['stage2_bytes_before']} bytes before, {report['stage2_bytes']} after")
    print(f"packed stage 2:   {report['packed_bytes_before']} bytes before, {report['packed_bytes']} after")
    print(f"free ROM bytes:   {report['free_bytes_before']} before, {report['free_bytes']} after")
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--base', choices=('pxs6', 'r1'), default='pxs6')
    ap.add_argument('--with-mark', action='store_true', help='on the stage2-mark text (the gate routine: --base r1 --with-mark)')
    a = ap.parse_args()
    build(a.output, a.base, a.with_mark)
