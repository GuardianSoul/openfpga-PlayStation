#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""The save routine of the next big build: exact voices (PXS9) + R1 + stage2-mark +
load-check + spu-quiet, as text transforms on the PXS6 text.

Order: PXS6 -> exact voices -> R1 -> stage2-mark -> load-check -> spu-quiet.
The exact-voices transform changes two lines (the MAGIC equate and the SPU port table);
no anchor of R1, stage2-mark, load-check or spu-quiet is on either line, and none of
their texts adds a line the exact-voices anchors could match, so the steps commute with
it: build() also applies exact voices last and requires the same text. The format step
goes first so every later step is reviewed on the base format's text. R1 comes before
load-check (the check sits between R1's magic compare and its remount) and before
stage2-mark only by convention (they touch different lines).
Usage: prepare_save_big_build_routine.py --output OUT
"""
import argparse
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

STEPS = ('exact-voices', 'r1', 'stage2-mark', 'load-check', 'spu-quiet')


def transforms():
    import prepare_spu_exact_voices, prepare_save_r1, prepare_save_stage2_mark, prepare_save_load_check, prepare_save_spu_quiet
    return {'exact-voices': prepare_spu_exact_voices.firmware_text, 'r1': prepare_save_r1.firmware_text,
            'stage2-mark': prepare_save_stage2_mark.firmware_text, 'load-check': prepare_save_load_check.firmware_text,
            'spu-quiet': prepare_save_spu_quiet.firmware_text}


def firmware_text(pxs6_text, order=STEPS):
    table = transforms()
    for name in order:
        pxs6_text = table[name](pxs6_text)
    return pxs6_text


def build(output_dir):
    from build_staged_save_fw import image_parts, rom1, rom2, rom40_hex, free_bytes
    from prepare_save_r1 import stub_bytes, packed_bytes
    from prepare_spu_exact_voices import pxs6_firmware
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    pxs6 = pxs6_firmware()
    text = firmware_text(pxs6)
    if firmware_text(pxs6, STEPS[1:] + STEPS[:1]) != text:
        raise ValueError('the exact-voices transform does not commute with the other steps')
    rows = []
    partial = pxs6
    table = transforms()
    for name in ('pxs6',) + STEPS:
        if name != 'pxs6':
            partial = table[name](partial)
        source = output_dir / f'staged-{len(rows)}-{name}.S'
        source.write_text(partial)
        image, stage = image_parts(source=source)           # stops if the packed stage 2 does not fit
        stub = stub_bytes(source)
        rows.append((name, len(stub), len(stage), packed_bytes(image, len(stub)), free_bytes(image)))
    source = output_dir / 'staged-big-build.S'
    source.write_text(text)
    image, stage = image_parts(source=source)
    rom = output_dir / 'psx_save_rom40.hex'
    rom.write_text(rom40_hex(rom1(image), rom2(image)))
    print('step            stub  stage 2  packed  free ROM bytes')
    for row in rows:
        print(f'{row[0]:14s} {row[1]:5d} {row[2]:8d} {row[3]:7d} {row[4]:6d}')
    print(f'routine: {source}')
    print(f'source sha256: {hashlib.sha256(source.read_bytes()).hexdigest()}')
    print(f'ROM sha256:    {hashlib.sha256(rom.read_bytes()).hexdigest()}')
    return rows


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--output', type=Path, required=True)
    build(ap.parse_args().output)
