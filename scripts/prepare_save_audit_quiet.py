#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "audit-quiet" (docs/save-states-finish/audit-fixes.md): two quiet-point fixes from
the 0.5.9 audit (audit-059.md findings 6 and 7). ROM only; anchors on the quiet point and mdec_quiet,
which no other step after the big build changes, so it chains anywhere after mdec-tap.

  gpu-fifo  (finding 7) the GPU quiet point needs GPUSTAT 26 (ready for a command) AND 28 (the command
            FIFO empty, gpu.vhd: GPUSTAT_ReadyRecDMA <= fifoIn_Empty). Bit 26 alone is 1 for a few
            clk2x after a command with words still queued, and E1-E6 still in the FIFO would be saved
            old. The game is stopped in the trap and DMA 2 is checked next, so the FIFO only drains:
            the condition is met within the same QUIET_POLLS looks. +1 instruction.
  mdec-all  (finding 6) a busy MDEC makes every retry wait (mdec_quiet answers "busy" in both halves of
            the retries). Before, from retry RETRY_MAX/2 on a busy MDEC was saved anyway, without the
            table tap, and the load's MDEC reset dropped the macroblock in flight. A MDEC busy for the
            whole retry time (about 0.5 s with no DMA) now ends the request with "State failed"
            instead. 0 instructions.

Usage: prepare_save_audit_quiet.py --routine SRC.S --output DIR [--parts gpu-fifo mdec-all]
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'audit-quiet'
PARTS = ('gpu-fifo', 'mdec-all')

EDITS = {
    'gpu-fifo': [("""        srl     $t0, $t0, 26
        andi    $t0, $t0, 1
        beqz    $t0, 3b
""", """        srl     $t0, $t0, 26
        andi    $t0, $t0, 5             # audit-quiet gpu-fifo: GPUSTAT 26 (ready for a command)
        xori    $t0, $t0, 5             # and 28 (command FIFO empty) both set
        bnez    $t0, 3b
""")],
    'mdec-all': [("""81:     jr      $ra
        move    $v0, $t2
""", """81:     jr      $ra
        ori     $v0, $zero, 1           # audit-quiet mdec-all: busy is a retry in every half
"""), ("""        jal     mdec_quiet              # R1: no MDEC command, no MDEC data on its way (t2 = first half)
""", """        jal     mdec_quiet              # R1: no MDEC command, no MDEC data on its way (any retry)
""")],
}

# Planted faults (audit-fixes.md lists which check catches each).
FAULTS = {
    'gpu-bit26-only': [("""        andi    $t0, $t0, 5             # audit-quiet gpu-fifo: GPUSTAT 26 (ready for a command)
        xori    $t0, $t0, 5             # and 28 (command FIFO empty) both set
""", """        andi    $t0, $t0, 1             # audit-quiet gpu-fifo: GPUSTAT 26 (ready for a command)
        xori    $t0, $t0, 1             # and 28 (command FIFO empty) both set
""")],
    'gpu-bit28-only': [("""        andi    $t0, $t0, 5             # audit-quiet gpu-fifo: GPUSTAT 26 (ready for a command)
        xori    $t0, $t0, 5             # and 28 (command FIFO empty) both set
""", """        andi    $t0, $t0, 4             # audit-quiet gpu-fifo: GPUSTAT 26 (ready for a command)
        xori    $t0, $t0, 4             # and 28 (command FIFO empty) both set
""")],
    'mdec-first-half': [("""        ori     $v0, $zero, 1           # audit-quiet mdec-all: busy is a retry in every half
""", """        move    $v0, $t2                # audit-quiet mdec-all: busy is a retry in every half
""")],
}


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


def firmware_text(text, parts=PARTS):
    for part in parts:
        for old, new in EDITS[part]:
            text = once(text, old, new)
    return text


def fault_text(text, name):
    for old, new in FAULTS[name]:
        text = once(text, old, new)
    return text


def build(text, output_dir, source, parts=PARTS):
    import prepare_save_rom_ext as ext
    fw = ext.builder()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = {}
    for name, body in (('before', text), (TAG, firmware_text(text, parts))):
        path = output_dir / f'staged-{name}.S'
        path.write_text(body)
        image, stage, extension = fw.image_parts_ext(source=path)
        rows[name] = dict(source=str(path), source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                          stage2_bytes=len(stage), free_bytes=fw.free_bytes(image, extension))
        if name == TAG:
            (output_dir / 'psx_save_rom40.hex').write_text(fw.rom40_hex(fw.rom1(image), fw.rom2(image)))
            (output_dir / ext.EXT_NAME).write_text(ext.ext_hex(extension))
    if set(parts) == set(PARTS):
        for name in FAULTS:
            faulty = output_dir / f'staged-fault-{name}.S'
            faulty.write_text(fault_text(firmware_text(text, parts), name))
            fw.image_parts_ext(source=faulty)
    a, b = rows['before'], rows[TAG]
    report = dict(rows, chain=source, parts=list(parts), packed_bytes_added=a['free_bytes'] - b['free_bytes'],
                  free_bytes=b['free_bytes'],
                  rom40_sha256=hashlib.sha256((output_dir / 'psx_save_rom40.hex').read_bytes()).hexdigest(),
                  ext_sha256=hashlib.sha256((output_dir / ext.EXT_NAME).read_bytes()).hexdigest())
    (output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    for key in ('packed_bytes_added', 'free_bytes'):
        print(f'{TAG}: {key} = {report[key]}')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--routine', type=Path, metavar='SRC.S', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--parts', nargs='+', choices=PARTS, default=list(PARTS))
    args = parser.parse_args()
    try:
        build(args.routine.read_text(), args.output, str(args.routine), tuple(args.parts))
    except ValueError as error:
        raise SystemExit(str(error))
