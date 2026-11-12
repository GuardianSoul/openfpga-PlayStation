#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "size-mib" (docs/save-states-finish/size-mib.md): the page count REPORTED to the
save device (SIZE_LO/SIZE_HI, 0x1818/9; the Pocket copies pages x 2,048 bytes) is rounded up to
whole MiB (512 pages), on the routine with the early-load step (prepare_save_early_load.py), ROM only.

Nothing stored changes: page 0 word 38 stays the true stored page count (the load checks it against
the bitmap) and the pages are where they were. The file only gets longer: the Pocket also copies the
buffer pages behind the state up to the MiB boundary (whatever they held: an earlier, longer state's
pages or the load's scratch pages; no load reads them). The largest state is 1,801 pages
(1,024 RAM + 512 VRAM + 256 SPU + 9), reported as 2,048 = the whole 4 MiB buffer, never more.
A load does not look at the file's length: files of the exact size (earlier routines) and padded
ones load alike.

Usage: prepare_save_size_mib.py --routine OUT    (sizes, ROM image, faults assemble)
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'save-size-mib'
MIB_PAGES = 512
BUFFER_PAGES = 2048
MAX_STATE_PAGES = 1024 + 512 + 256 + 9


def reported_pages(stored):
    return (stored + MIB_PAGES - 1) // MIB_PAGES * MIB_PAGES


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


STORE = '        sw      $s7, PAGE+4*38($k0)\n'
SIZE_OLD = '''\
        sb      $s7, SIZE_LO($k0)
        srl     $t0, $s7, 8
        sb      $t0, SIZE_HI($k0)
'''
SIZE_NEW = '''\
        # size-mib: the size reported (the Pocket copies it) is whole MiB, 512 pages; word 38
        # above stays the true count. Low byte 0, high byte 2 x MiB (at most 8: 2,048 pages).
        addiu   $t0, $s7, 511
        srl     $t0, $t0, 9
        sll     $t0, $t0, 1
        sb      $zero, SIZE_LO($k0)
        sb      $t0, SIZE_HI($k0)
'''

FAULTS = {
    # the rounded count is also the one stored in page 0 (a load would refuse the state)
    'stored-rounded': (STORE, '''\
        addiu   $t0, $s7, 511           # FAULT stored-rounded
        srl     $t0, $t0, 9
        sll     $t0, $t0, 9
        sw      $t0, PAGE+4*38($k0)
'''),
    # an exact multiple of 512 pages gets one more MiB
    'one-mib-more': ('        addiu   $t0, $s7, 511\n', '        addiu   $t0, $s7, 512           # FAULT one-mib-more\n'),
}


def firmware_text(text):
    """The routine with the early-load step, with the reported size in whole MiB. Exact anchors."""
    if 'size-mib' in text:
        raise ValueError(f'{TAG}: the routine already has the step')
    if text.count(STORE) != 1:
        raise ValueError(f'{TAG}: the stored page count (word 38) is not written once')
    return once(text, SIZE_OLD, SIZE_NEW)


def fault_text(text, name):
    old, new = FAULTS[name]
    return once(text, old, new)


def bench_text(text):
    """tb_staged_save_cpu.vhd: the size the routine reports is the stored pages in whole MiB."""
    return once(text, 'assert size_pages = stored report', f'assert size_pages = (stored + {MIB_PAGES - 1}) / {MIB_PAGES} * {MIB_PAGES} report')


def base_text():
    from prepare_save_early_load import base_text as before, firmware_text as early
    return early(before())


def build(output_dir):
    """The routine without and with the step: sizes; the ROM image with it."""
    from build_staged_save_fw import image_parts, rom1, rom2, rom40_hex, free_bytes
    from prepare_save_r1 import stub_bytes, packed_bytes
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = {}
    for name, text in (('early-load', base_text()), ('size-mib', firmware_text(base_text()))):
        source = output_dir / f'staged-{name}.S'
        source.write_text(text)
        image, stage = image_parts(source=source)           # stops if the packed stage 2 does not fit
        stub = stub_bytes(source)
        rows[name] = dict(source=str(source), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), stub_bytes=len(stub),
                          stage2_bytes=len(stage), packed_bytes=packed_bytes(image, len(stub)), free_bytes=free_bytes(image))
    for name in FAULTS:                                     # every planted fault applies and assembles
        faulty = output_dir / f'staged-size-mib-fault-{name}.S'
        faulty.write_text(fault_text(firmware_text(base_text()), name))
        image_parts(source=faulty)
    rom = output_dir / 'psx_save_rom40.hex'
    image, _ = image_parts(source=output_dir / 'staged-size-mib.S')
    rom.write_text(rom40_hex(rom1(image), rom2(image)))
    a, b = rows['early-load'], rows['size-mib']
    if a['stub_bytes'] != b['stub_bytes']:
        raise ValueError(f'{TAG}: the stub changed')
    report = dict(rows, rom=str(rom), rom_sha256=hashlib.sha256(rom.read_bytes()).hexdigest(),
                  packed_bytes_added=b['packed_bytes'] - a['packed_bytes'],
                  free_bytes_before=a['free_bytes'], free_bytes=b['free_bytes'],
                  max_state_pages=MAX_STATE_PAGES, max_reported_pages=reported_pages(MAX_STATE_PAGES), buffer_pages=BUFFER_PAGES)
    (output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    for name, row in rows.items():
        print(f"{name:12} stage 2 {row['stage2_bytes']} bytes, packed {row['packed_bytes']}, free {row['free_bytes']}")
    print(f"packed bytes added: {report['packed_bytes_added']}")
    print(f"source sha256: {b['source_sha256']}\nROM sha256:    {report['rom_sha256']}")
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--routine', type=Path, metavar='OUT', required=True)
    args = parser.parse_args()
    try:
        build(args.routine)
    except ValueError as error:
        raise SystemExit(str(error))
