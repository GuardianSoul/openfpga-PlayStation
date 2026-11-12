#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "stage2-mark": reuse the unpacked stage 2 only after an unpack
since FPGA configuration (a routine-only change; no RTL change).

Design note, exit paths and bench results: docs/save-states-finish/stage2-mark.md.
The stub reuses the stage 2 copy at STAGE2 (SDRAM 0x200000) when its first word
equals the ROM's stamp. After a restart of the core or a Sleep the SDRAM was not
refreshed for a while: the stamp can survive while other bits have flipped, and the
stub then runs damaged code. The step adds a marker in the staging page (word 508,
MARK), a block RAM that configuration clears:
  - marker not MARK_OK: unpack, whatever the stamp says (the marker is cleared first,
    so an unpack that does not end leaves none), and clear RETRIES, the other page word
    kept between requests. Nothing relies on the page being zero after configuration,
    only on word 508 not being MARK_OK;
  - marker MARK_OK: the stamp compare as before;
  - MARK_OK is written when stage 2 is entered (unpacked now, or reused) and at the
    end of every request (restore:), after stage 2's last page copy, because every
    page copy overwrites the whole staging page.
Only the stub's text changes (10 instructions, 40 bytes). Stage 2 keeps its text and length;
its jumps back to the stub carry the moved addresses, so its stamp changes.
The transform applies to the PXS6 text (prepare_spu_exact_voices.pxs6_firmware()),
to the R1 text (prepare_save_r1.firmware_text of it) and to staged_handler.S itself.
Usage: prepare_save_stage2_mark.py --output OUT [--base pxs6|r1]
"""
import argparse
import difflib
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'stage2-mark anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


def after(text, anchor, added):
    return once(text, anchor, anchor + added)


EQUATES = '''\
        # stage2-mark (scripts/prepare_save_stage2_mark.py, docs/save-states-finish/stage2-mark.md)
        .equ    MARK, PAGE+0x7F0        # staging page word 508: MARK_OK = stage 2 was unpacked since
        .equ    MARK_OK, 0x53324F4B     # FPGA configuration ("KO2S"; the page is zero after it)
'''
EQUATES_ANCHOR = '        .equ    DIAG_ALWAYS, PAGE+0x7F4 # DIAG: conditions failed at every retry (word 509)\n'

# the reuse decision
DECIDE_OLD = '''\
        lw      $t2, 0($t1)
        lw      $t4, 0($t3)
        lui     $t5, 0xBF00
        beq     $t2, $t4, 5f
        ori     $t6, $t5, 0xFF4         # the first ROM's part ends at 0xFF4 ...
        ori     $t1, $t1, 0x8000
'''
DECIDE_NEW = '''\
        lw      $t2, 0($t1)
        lw      $t4, 0($t3)
        # stage2-mark: the copy at STAGE2 counts only after an unpack since configuration.
        # After a restart or a Sleep the SDRAM was not refreshed: the stamp can be there
        # with other bits flipped. t8 = MARK_OK up to label 5 (the unpacker leaves it).
        lw      $t0, MARK($k0)
        lui     $t8, MARK_OK >> 16
        ori     $t8, $t8, MARK_OK & 0xFFFF
        bne     $t0, $t8, 20f           # no marker: unpack, whatever the stamp says
        lui     $t5, 0xBF00             # (delay slot: on both ways)
        beq     $t2, $t4, 5f
20:     ori     $t6, $t5, 0xFF4         # the first ROM's part ends at 0xFF4 ... (the beq's delay slot)
        sw      $zero, MARK($k0)        # unpacking: no marker until it is done
        sw      $zero, RETRIES($k0)     # no marker: nothing in the page is from this session, so the
        ori     $t1, $t1, 0x8000        # retry count starts at 0 whatever the page held
'''

# stage 2 is entered: unpacked now, or reused with the marker set
ENTER_OLD = '''\
5:
        lui     $t0, %hi(quiet)
'''
ENTER_NEW = '''\
5:
        sw      $t8, MARK($k0)          # stage2-mark: unpacked (a retry before a page copy reuses it)
        lui     $t0, %hi(quiet)
'''

# the end of a request: stage 2's page copies have overwritten the staging page
END_OLD = '''\
        sw      $zero, RETRIES($k0)     # the request ended: the next one starts at 0
restore_kept:
'''
END_NEW = '''\
        sw      $zero, RETRIES($k0)     # the request ended: the next one starts at 0
        lui     $t0, MARK_OK >> 16      # stage2-mark: after the last page copy (each one
        ori     $t0, $t0, MARK_OK & 0xFFFF      # overwrites the whole staging page)
        sw      $t0, MARK($k0)
restore_kept:
'''

# Deliberate faults for the bench (never a fitted input): name -> (old, new) on the step's text
FAULTS = {
    # (1) the marker test removed: the stamp alone decides again, a damaged copy is accepted
    'mark-test-removed': ('        bne     $t0, $t8, 20f           # no marker: unpack, whatever the stamp says\n',
                          '        nop                             # FAULT: the marker is not tested\n'),
    # (1b) the marker test inverted: unpack only when the marker IS there
    'mark-test-inverted': ('        bne     $t0, $t8, 20f           # no marker: unpack, whatever the stamp says\n',
                           '        beq     $t0, $t8, 20f           # FAULT: inverted\n'),
    # (2) the marker is not written again at the end of a request: every later request unpacks
    'mark-not-rewritten': ('        sw      $t0, MARK($k0)\nrestore_kept:\n',
                           '        nop                             # FAULT: no marker at the end\nrestore_kept:\n'),
    # (3) the marker is not written when stage 2 is entered: every retry unpacks again
    'mark-not-set-at-entry': ('        sw      $t8, MARK($k0)          # stage2-mark: unpacked (a retry before a page copy reuses it)\n',
                              '        nop                             # FAULT: no marker after the unpack\n'),
}


def firmware_text(text):
    """text: the PXS6 or the R1 routine (or staged_handler.S) -> the same with the marker.

    Every change is an exact-anchor replacement; a missing or repeated anchor raises."""
    if 'MARK_OK' in text:
        raise ValueError('stage2-mark is applied already')
    import re
    if re.search(r'^20:|[ ,]20[fb]\b', text, flags=re.M):
        raise ValueError('numeric label 20 is in use')
    text = after(text, EQUATES_ANCHOR, EQUATES)
    text = once(text, DECIDE_OLD, DECIDE_NEW)
    text = once(text, ENTER_OLD, ENTER_NEW)
    text = once(text, END_OLD, END_NEW)
    return text


def fault_text(text, name):
    """The step's text with one deliberate fault (bench only)."""
    old, new = FAULTS[name]
    return once(text, old, new)


def base_text(base):
    from prepare_spu_exact_voices import pxs6_firmware
    text = pxs6_firmware()
    if base == 'r1':
        from prepare_save_r1 import firmware_text as r1
        text = r1(text)
    elif base != 'pxs6':
        raise ValueError('base: pxs6 or r1')
    return text


def disassemble(source):
    """The stub (.text) as a list of 'mnemonic operands' lines, addresses left out."""
    import subprocess, tempfile
    from build_staged_save_fw import LINK
    from tool_paths import find_tool
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'h.ld').write_text(LINK)
        subprocess.run([find_tool('llvm-mc'), '-triple=mipsel', '-mcpu=mips1', '-filetype=obj', str(source), '-o', str(tmp / 'h.o')], check=True)
        subprocess.run([find_tool('ld.lld'), '-m', 'elf32ltsmip', '-T', str(tmp / 'h.ld'), '-e', 'entry', '--no-dynamic-linker',
                        '-o', str(tmp / 'h.elf'), str(tmp / 'h.o')], check=True)
        objdump = find_tool('llvm-objdump')
        if not objdump:
            return None
        out = subprocess.run([objdump, '-d', '-j', '.text', '--no-show-raw-insn', '--no-leading-addr', str(tmp / 'h.elf')],
                             check=True, capture_output=True, text=True).stdout
    lines = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.endswith(':') or line.startswith(('Disassembly', '/')) or 'file format' in line:
            continue
        lines.append(' '.join(line.split()))
    return lines


def stub_diff(old_source, new_source):
    """(instructions added, instructions removed, diff lines). Branch and jump targets move
    with the inserted code, so operands that are addresses are masked before the compare."""
    import re
    mask = lambda rows: [re.sub(r'(0x[0-9a-f]+|\b[0-9a-f]{6,8}\b)( <[^>]*>)?$', '<target>', row) for row in rows]
    old, new = disassemble(old_source), disassemble(new_source)
    if old is None or new is None:
        return None, None, []
    rows = [row for row in difflib.unified_diff(mask(old), mask(new), 'stub before', 'stub after', lineterm='', n=2)]
    added = sum(1 for row in rows if row.startswith('+') and not row.startswith('+++'))
    removed = sum(1 for row in rows if row.startswith('-') and not row.startswith('---'))
    return added, removed, rows


def build(output_dir, base='pxs6'):
    from build_staged_save_fw import image_parts, rom1, rom2, rom40_hex, free_bytes
    from prepare_save_r1 import stub_bytes, packed_bytes
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    old_source = output_dir / f'staged-{base}.S'
    old_source.write_text(base_text(base))
    source = output_dir / f'staged-{base}-mark.S'
    source.write_text(firmware_text(old_source.read_text()))
    old_image, old_stage = image_parts(source=old_source)
    image, stage = image_parts(source=source)           # stops if the packed stage 2 does not fit
    old_stub, stub = stub_bytes(old_source), stub_bytes(source)
    # Stage 2's text is the same; its jumps back into the stub (STUB restore / restore_kept:
    # a lui/ori pair each) carry the moved addresses, so those words and the stamp differ.
    if len(old_stage) != len(stage):
        raise ValueError('stage2-mark changed the length of stage 2: only the stub may change')
    moved = [i for i in range(4, len(stage), 4) if old_stage[i:i + 4] != stage[i:i + 4]]
    for i in moved:
        old_word, word = (int.from_bytes(s[i:i + 4], 'little') for s in (old_stage, stage))
        if (old_word ^ word) >> 16 or old_word >> 26 != 0x0D:      # ori $t9, $t9, %lo(stub label)
            raise ValueError(f'stage 2 word {i // 4} changed and is not the low half of a stub address')
    if len(stub) % 4 or len(old_stub) % 4:
        raise ValueError('stub length is not a multiple of 4')
    added, removed, rows = stub_diff(old_source, source)
    if added is not None and (removed != 0 or 4 * added != len(stub) - len(old_stub)):
        raise ValueError(f'stub diff: {added} added, {removed} removed, {len(stub) - len(old_stub)} bytes more')
    for name in FAULTS:                                 # every planted fault applies and assembles
        faulty = output_dir / f'staged-{base}-mark-fault-{name}.S'
        faulty.write_text(fault_text(source.read_text(), name))
        if len(stub_bytes(faulty)) != len(stub):
            raise ValueError(f'fault {name} changed the stub length')
    rom = output_dir / 'psx_save_rom40.hex'
    rom.write_text(rom40_hex(rom1(image), rom2(image)))
    report = dict(base=base, source=str(source), rom=str(rom),
                  base_sha256=hashlib.sha256(old_source.read_bytes()).hexdigest(),
                  source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  rom_sha256=hashlib.sha256(rom.read_bytes()).hexdigest(),
                  stub_bytes_before=len(old_stub), stub_bytes=len(stub), stub_instructions_added=added,
                  stage2_bytes=len(stage), stage2_stub_address_words=len(moved),
                  packed_bytes_before=packed_bytes(old_image, len(old_stub)), packed_bytes=packed_bytes(image, len(stub)),
                  free_bytes_before=free_bytes(old_image), free_bytes=free_bytes(image))
    print(f"stage2-mark on {base}: {source}")
    print(f"stub:             {report['stub_bytes_before']} bytes before, {report['stub_bytes']} after "
          f"({added} instructions added, {removed} removed)")
    print(f"unpacked stage 2: {report['stage2_bytes']} bytes, as before; {len(moved)} words differ, each the low half "
          f"of a stub address (restore, restore_kept); new stamp")
    print(f"packed stage 2:   {report['packed_bytes_before']} bytes before, {report['packed_bytes']} after")
    print(f"free ROM bytes:   {report['free_bytes_before']} before, {report['free_bytes']} after")
    print('stub instruction diff (branch targets masked):')
    for row in rows:
        print('  ' + row)
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--base', choices=('pxs6', 'r1'), default='pxs6')
    a = ap.parse_args()
    build(a.output, a.base)
