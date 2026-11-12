#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "rom-room" (docs/save-states-finish/rom-room.md): more free bytes in the routine ROM.
ROM only, no RTL; stage 2 is not changed (its unpacked bytes are the same), only the stub and the packing:

  lz2     the stub's unpacker reads lz2_pack's stream (build_staged_save_fw.py): matches of 2-17 bytes,
          halfwords swapped (the unpacker stores stream byte p at p ^ 2), optimal parse. The builder
          picks the format by the label lz2. Same instruction count as the old unpacker.
  cop0    the debug COP0 registers (3, 5, 7, 9, 11) are saved and restored with the load delays filled
          by the next register's load instead of nops: 10 instructions less, same order of writes.

Chained after every other step (it only touches the stub's unpacker and the two COP0 blocks).

Usage: prepare_save_rom_room.py --routine SRC.S --output DIR   (the routine with the step, both ROM files,
           report.json with the free bytes before and after)
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'rom-room'

ENTRY_OLD = """        .irp    n, 3,5,7,9,11
        mfc0    $t0, $\\n
        nop
        sw      $t0, PAGE+4*(43+(\\n-3)/2)($k0)
        .endr
"""
ENTRY_NEW = """        mfc0    $t3, $3                 # rom-room: each load delay holds the next register's read
        mfc0    $t4, $5                 # (t3 and t4 are set again below before any use)
        sw      $t3, PAGE+4*43($k0)
        mfc0    $t3, $7
        sw      $t4, PAGE+4*44($k0)
        mfc0    $t4, $9
        sw      $t3, PAGE+4*45($k0)
        mfc0    $t3, $11
        sw      $t4, PAGE+4*46($k0)
        sw      $t3, PAGE+4*47($k0)
"""
RESTORE_OLD = """        .irp    n, 3,5,7,9,11
        lw      $t0, PAGE+4*(43+(\\n-3)/2)($k0)
        nop
        mtc0    $t0, $\\n
        .endr
"""
RESTORE_NEW = """        lw      $t0, PAGE+4*43($k0)     # rom-room: written in the same order (3, 5, 7, 9, 11),
        lw      $t1, PAGE+4*44($k0)     # each load delay holding the next load
        mtc0    $t0, $3
        lw      $t0, PAGE+4*45($k0)
        mtc0    $t1, $5
        lw      $t1, PAGE+4*46($k0)
        mtc0    $t0, $7
        lw      $t0, PAGE+4*47($k0)
        mtc0    $t1, $9
        mtc0    $t0, $11
"""
UNPACK_OLD = """        lw      $t6, 4($a0)             # unpacked length
        lui     $t1, %hi(STAGE2)
        addu    $t6, $t6, $t1
        addiu   $a0, $a0, 8
        # LZSS: a flag byte per 8 items (bit 0 first), 1 = a literal byte,
        # 0 = a match: 12-bit distance, length 3-18
6:      lbu     $t7, 0($a0)
        addiu   $a0, $a0, 1
        ori     $t7, $t7, 0x100         # (this bit ends the group)
7:      andi    $t0, $t7, 1
        beqz    $t0, 8f
        srl     $t7, $t7, 1
        lbu     $t0, 0($a0)
        addiu   $a0, $a0, 1
        sb      $t0, 0($t1)
        b       9f
        addiu   $t1, $t1, 1
8:      lbu     $t0, 0($a0)
        lbu     $t2, 1($a0)
        addiu   $a0, $a0, 2
        srl     $t4, $t2, 4
        sll     $t4, $t4, 8
        or      $t4, $t4, $t0
        andi    $t2, $t2, 15
        addiu   $t2, $t2, 3
        subu    $t4, $t1, $t4
1:      lbu     $t0, 0($t4)
        addiu   $t4, $t4, 1
        addiu   $t2, $t2, -1
        sb      $t0, 0($t1)
        bnez    $t2, 1b
        addiu   $t1, $t1, 1
9:      beq     $t1, $t6, 5f
        li      $t0, 1
        bne     $t7, $t0, 7b
        nop
        b       6b
        nop
5:
"""
UNPACK_NEW = """lz2:                                    # rom-room (build_staged_save_fw.py: with this label the stream is lz2_pack's)
        lw      $t6, 4($a0)             # unpacked length
        lui     $t1, %hi(STAGE2)
        addu    $t6, $t6, $t1
        addiu   $a0, $a0, 8
        # LZSS: a flag byte per 8 items (bit 0 first), 1 = a literal byte,
        # 0 = a match: 12-bit distance, length 2-17. t1 counts the stream's
        # bytes; byte p is stored at p ^ 2 (the stream has each word's
        # halfwords swapped) and a match reads its source the same way
6:      lbu     $t7, 0($a0)
61:     addiu   $a0, $a0, 1
        ori     $t7, $t7, 0x100         # (this bit ends the group)
7:      andi    $t0, $t7, 1
71:     srl     $t7, $t7, 1
        lbu     $t2, 0($a0)             # the literal, or distance bits 7:0
        bnez    $t0, 3f
        addiu   $a0, $a0, 1
        lbu     $t0, 0($a0)             # distance bits 11:8 << 4 | length - 2
        addiu   $a0, $a0, 1
        srl     $t4, $t0, 4
        sll     $t4, $t4, 8
        or      $t4, $t4, $t2
        andi    $t2, $t0, 15
        addiu   $t2, $t2, 2
        subu    $t4, $t1, $t4
1:      xori    $t0, $t4, 2
        lbu     $t0, 0($t0)
        addiu   $t4, $t4, 1
        xori    $t5, $t1, 2
        sb      $t0, 0($t5)
        addiu   $t2, $t2, -1
        bnez    $t2, 1b
        addiu   $t1, $t1, 1
9:      beq     $t1, $t6, 5f
        li      $t0, 1
        bne     $t7, $t0, 71b           # (the delay slot is 7's first instruction)
        andi    $t0, $t7, 1
        b       61b                     # (the delay slot is 6's first instruction)
        lbu     $t7, 0($a0)
3:      xori    $t5, $t1, 2             # a literal
        sb      $t2, 0($t5)
        b       9b
        addiu   $t1, $t1, 1
5:
"""

# Planted faults: each must make a bench fail (rom-room.md lists which bench catches which).
FAULTS = {
    'length-3': [('        addiu   $t2, $t2, 2\n        subu    $t4, $t1, $t4\n',
                  '        addiu   $t2, $t2, 3\n        subu    $t4, $t1, $t4\n')],
    'literal-unswapped': [('3:      xori    $t5, $t1, 2             # a literal\n',
                           '3:      move    $t5, $t1                # a literal\n')],
    'source-unswapped': [('1:      xori    $t0, $t4, 2\n        lbu     $t0, 0($t0)\n',
                          '1:      move    $t0, $t4\n        lbu     $t0, 0($t0)\n')],
    'restore-9-from-11': [('        lw      $t0, PAGE+4*47($k0)\n        mtc0    $t1, $9\n',
                           '        lw      $t0, PAGE+4*47($k0)\n        mtc0    $t0, $9\n')],
    'save-7-from-5': [('        mfc0    $t3, $7\n        sw      $t4, PAGE+4*44($k0)\n        mfc0    $t4, $9\n        sw      $t3, PAGE+4*45($k0)\n',
                       '        mfc0    $t3, $7\n        sw      $t4, PAGE+4*44($k0)\n        mfc0    $t4, $9\n        sw      $t4, PAGE+4*45($k0)\n')],
}


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


def firmware_text(text):
    for old, new in ((ENTRY_OLD, ENTRY_NEW), (RESTORE_OLD, RESTORE_NEW), (UNPACK_OLD, UNPACK_NEW)):
        text = once(text, old, new)
    return text


def fault_text(text, name):
    for old, new in FAULTS[name]:
        text = once(text, old, new)
    return text


def symbols(source):
    """{label: address} of the linked routine (the builder's tools and link script)."""
    import subprocess
    import tempfile
    import prepare_save_rom_ext as ext
    from tool_paths import find_tool
    fw = ext.builder()
    mc, ld, nm = (find_tool(t) for t in ('llvm-mc', 'ld.lld', 'llvm-nm'))
    with tempfile.TemporaryDirectory() as tmp:
        obj, elf, script = Path(tmp) / 'h.o', Path(tmp) / 'h.elf', Path(tmp) / 'h.ld'
        script.write_text(fw.LINK)
        subprocess.run([mc, '-triple=mipsel', '-mcpu=mips1', '-filetype=obj', str(source), '-o', str(obj)], check=True)
        subprocess.run([ld, '-m', 'elf32ltsmip', '-T', str(script), '-e', 'entry', '--no-dynamic-linker', '-o', str(elf), str(obj)],
                       check=True)
        lines = subprocess.run([nm, str(elf)], check=True, capture_output=True, text=True).stdout.splitlines()
    return {n: int(a, 16) for a, _, n in (l.split() for l in lines if len(l.split()) == 3)}


def stage2_moves(old, new, old_syms, new_syms):
    """Stage 2 before and after the step: equal but for the stub addresses it names (STUB's
    lui/ori pair), which move by the stub's change. Every differing word must be an ori of
    t9 whose old operand is a stub label's old address and whose new one that label's new
    address. -> the number of such words."""
    stub = {old_syms[n] & 0xFFFF: new_syms[n] & 0xFFFF for n in old_syms
            if n in new_syms and 0xBF000000 <= old_syms[n] < 0xBF001000}
    if len(old) != len(new):
        raise ValueError(f'{TAG}: stage 2 length changed')
    moved = 0
    for i in range(0, len(old), 4):
        a, b = (int.from_bytes(x[i:i + 4], 'little') for x in (old, new))
        if i == 0 or a == b:                          # word 0: the stamp (CRC-32 of the rest)
            continue
        if a >> 16 != 0x3739 or b >> 16 != 0x3739 or stub.get(a & 0xFFFF) != b & 0xFFFF:
            raise ValueError(f'{TAG}: stage 2 word {i:#x} changed: {a:08x} -> {b:08x}')
        moved += 1
    return moved


def build(text, output_dir, source):
    """The routine without and with the step: sizes, both ROM files, stage 2 unchanged; every fault assembles."""
    import prepare_save_rom_ext as ext
    from prepare_save_r1 import stub_bytes
    fw = ext.builder()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows, stages = {}, {}
    for name, body in (('before', text), (TAG, firmware_text(text))):
        path = output_dir / f'staged-{name}.S'
        path.write_text(body)
        image, stage, extension = fw.image_parts_ext(source=path)
        stages[name] = stage
        rows[name] = dict(source=str(path), source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                          stub_bytes=len(stub_bytes(path)), stage2_bytes=len(stage), free_bytes=fw.free_bytes(image, extension),
                          extension_bytes_used=len(extension.rstrip(b'\0')))
        if name == TAG:
            (output_dir / 'psx_save_rom40.hex').write_text(fw.rom40_hex(fw.rom1(image), fw.rom2(image)))
            (output_dir / ext.EXT_NAME).write_text(ext.ext_hex(extension))
    moved = stage2_moves(stages['before'], stages[TAG], symbols(output_dir / 'staged-before.S'),
                         symbols(output_dir / f'staged-{TAG}.S'))
    for name in FAULTS:
        faulty = output_dir / f'staged-fault-{name}.S'
        faulty.write_text(fault_text(firmware_text(text), name))
        fw.image_parts_ext(source=faulty)
    a, b = rows['before'], rows[TAG]
    report = dict(rows, chain=source, stage2_words_moved=moved, stub_bytes_saved=a['stub_bytes'] - b['stub_bytes'],
                  free_bytes_before=a['free_bytes'], free_bytes=b['free_bytes'],
                  rom40_sha256=hashlib.sha256((output_dir / 'psx_save_rom40.hex').read_bytes()).hexdigest(),
                  ext_sha256=hashlib.sha256((output_dir / ext.EXT_NAME).read_bytes()).hexdigest())
    (output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    for key in ('stub_bytes_saved', 'free_bytes_before', 'free_bytes'):
        print(f'{TAG}: {key} = {report[key]}')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--routine', type=Path, metavar='SRC.S', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        build(args.routine.read_text(), args.output, str(args.routine))
    except ValueError as error:
        raise SystemExit(str(error))
