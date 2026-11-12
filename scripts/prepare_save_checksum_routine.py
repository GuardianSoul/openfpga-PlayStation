#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "checksum" (docs/save-states-finish/checksum-card.md): the stream's page copies get a
checksum on the save, compared on the load before anything of the game changes. ROM only; chained after
compress (routine g; test38 has no readout-hold, and the step applies with or without it).

NEEDS the RTL step prepare_save_checksum.py: CSUM (0x1830-0x1833, one lw) is the copy engine's rotated sum
of every buffer word since the last write to it; while a load request is open its bit 31 reads flipped if
the card slot dropped a bridge word since the previous request ended. On a core without it CSUM reads 0:
saves store 0, and a state with 0 is loaded without the compare (as one from before the step).

  save  `jal cz_save` gets the clear in its delay slot; right after it returns, CSUM (the stream's pages,
        written by cz_flush only) goes to s0, which pack_end stores as page 0 word 199 (CS_WORD) with the
        bitmap and CZ_LEN; page 0 is written after that, so its own copy is not in the sum.
  load  `jal cz_load` gets the clear in its delay slot (page 0 was read before it); at cz_end, after the
        length check and before page 0 is read again, CSUM against word 199, which cz_load took into
        v0 at its start (at cz_end the staging page holds the stream's last page, not page 0: reading
        word 199 there took a stream word, mostly 0, and skipped the compare). 0 in the state: no compare
        (a state from before the step, or a sum that came out 0). Else any difference -> `bad` (FINISH 2,
        the game as it was: nothing of the running game is changed before cz_load returns).
  States stored as pages (pXS9, from before compression) take cz_load's other path and are not compared.

What the sum does not cover: page 0 (its magic, the stored-page count against the bitmap and the stream
length are checked), page 1 (scratchpad, GTE) and the CD FIFO pages. A dropped upload word in those is
still caught by the overflow flag, which flips the compare whatever page it was in.

Usage: prepare_save_checksum_routine.py --routine SRC.S --output DIR   (SRC.S: routine g, or g + readout-hold)
       prepare_save_checksum_routine.py --chain --output DIR          (routine g built here)
CPU round trip: run_save_project_cpu.py ... --routine-step compress --routine-step checksum (save_checksum_bench.py)
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'checksum'


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


EQU_OLD = "        .equ    ID_WORD, PAGE+4*198      # page 0 word 198: DISC_ID at the save (0: a state from before the step)\n"
EQU_NEW = EQU_OLD + (
    "        .equ    CSUM, 0x1830            # checksum: the copy engine's sum (write: clear; prepare_save_checksum.py)\n"
    "        .equ    CS_LAST, PAGE+0x7FC     # page 0 word 511 (RETRIES while a request runs; restore zeroes it): T\n"
    "        .equ    CS_P1, 0x2FC            # scratchpad word below BITMAP: page 1's sum during a save\n")
# save: page 1 is put early (the scratchpad then holds only the bitmap): its sum waits below the bitmap
P1_OLD = ("        jal     put_page\n"
          "        li      $a0, CPU_PAGE\n"
          "        MARK    4\n"
          "        # the scratchpad is in page 1 now: the page bitmap goes there\n"
          "        lui     $t1, 0x1F80\n"
          "        li      $t0, 0x100\n")
P1_NEW = ("        sb      $zero, CSUM($k0)        # checksum: page 1's sum\n"
          "        jal     put_page\n"
          "        li      $a0, CPU_PAGE\n"
          "        MARK    4\n"
          "        # the scratchpad is in page 1 now: the page bitmap goes there\n"
          "        lui     $t1, 0x1F80\n"
          "        lw      $t2, CSUM($k0)          # checksum: page 1, kept below the bitmap until pack_end\n"
          "        li      $t0, 0x100\n"
          "        sw      $t2, CS_P1($t1)\n")
SAVE_OLD = ("        jal     cz_save                 # compress: v1 = the state's pages, s1 = the stream's bytes\n"
            "        nop\n"
            "        jal     get_page\n")
SAVE_NEW = ("        jal     cz_save                 # compress: v1 = the state's pages, s1 = the stream's bytes\n"
            "        sb      $zero, CSUM($k0)        # checksum: from the stream's first page copy on\n"
            "        lw      $s0, CSUM($k0)          # checksum: the stream's pages\n"
            "        jal     get_page\n")
BMP_OLD = ("        move    $a0, $zero\n"
           "        lui     $t1, 0x1F80\n"
           "        addiu   $t2, $k0, PAGE+0x100\n"
           "        li      $t3, 0x100\n")
BMP_NEW = ("        move    $a0, $zero\n"
           "        lui     $t1, 0x1F80\n"
           "        lw      $t4, CS_P1($t1)         # checksum: page 1's sum\n"
           "        addiu   $t2, $k0, PAGE+0x100\n"
           "        li      $t3, 0x100\n")
# page 0 goes out twice: with word 511 = 0 for its own sum, then with T there. The last word of a
# page enters the sum without a rotation after it: page 0 as loaded sums to its sum with 0 + T.
STORE_OLD = ("        sw      $s1, CZ_LEN($k0)        # compress\n"
             "        jal     put_page\n"
             "        move    $a0, $zero\n"
             "        sb      $v1, SIZE_LO($k0)       # compress: the pages the stream took, not the stored pages\n")
STORE_NEW = ("        sw      $s1, CZ_LEN($k0)        # compress\n"
             "        sw      $zero, CS_LAST($k0)     # checksum: word 511 is 0 in page 0's own sum\n"
             "        addu    $s0, $s0, $t4           # checksum: stream + page 1\n"
             "        sb      $zero, CSUM($k0)\n"
             "        jal     put_page\n"
             "        move    $a0, $zero\n"
             "        lw      $t4, CSUM($k0)          # checksum: page 0 (word 511 = 0)\n"
             "        sb      $v1, SIZE_LO($k0)       # compress: the pages the stream took, not the stored pages\n"
             "        addu    $s0, $s0, $t4           # checksum: T = stream + page 1 + page 0\n"
             "        sw      $s0, CS_LAST($k0)\n"
             "        jal     put_page                # checksum: page 0 again, T in word 511\n"
             "        move    $a0, $zero\n")
# load: pages 1 and 0 summed before anything else; v0 = what the stream's sum must be
GET_OLD = ("        jal     put_page                # keep the interrupted game's registers\n"
           "        li      $a0, SCRATCH\n"
           "        jal     get_page\n"
           "        move    $a0, $zero\n"
           "        lw      $t0, PAGE($k0)\n")
GET_NEW = ("        jal     put_page                # keep the interrupted game's registers\n"
           "        li      $a0, SCRATCH\n"
           "        sb      $zero, CSUM($k0)        # checksum: page 1 as loaded\n"
           "        jal     get_page\n"
           "        li      $a0, CPU_PAGE\n"
           "        lw      $v0, CSUM($k0)\n"
           "        sb      $zero, CSUM($k0)        # checksum: page 0 as loaded (T in word 511)\n"
           "        jal     get_page\n"
           "        move    $a0, $zero\n"
           "        lw      $t2, CSUM($k0)\n"
           "        lw      $t3, CS_LAST($k0)\n"
           "        addu    $v0, $v0, $t2\n"
           "        sll     $t3, $t3, 1\n"
           "        subu    $v0, $t3, $v0           # checksum: the stream's sum must be 2T - page 1 - page 0 (v0 kept to cz_end)\n"
           "        lw      $t0, PAGE($k0)\n")
LOAD_OLD = ("        jal     cz_load                 # compress: the stored pages into the shadow, or `bad`\n"
            "        nop\n")
LOAD_NEW = ("        jal     cz_load                 # compress: the stored pages into the shadow, or `bad`\n"
            "        sb      $zero, CSUM($k0)        # checksum: the stream's page copies from here\n")
# a state stored as pages ("pXS9"): its pages are in no sum. No released core wrote one: refused.
PAGES_OLD = ("        bnez    $at, cz_stream\n"
             "        addiu   $fp, $k0, PAGE+0x800\n"
             "1:      beq     $s0, $s1, cz_done       # pages as they are\n"
             "        move    $a0, $v1\n"
             "        jal     page_cmd\n"
             "        li      $t9, 3\n"
             "        jal     copy_in\n"
             "        li      $a1, 0x800\n"
             "        b       1b\n"
             "        addiu   $v1, $v1, 1\n"
             "cz_stream:\n")
PAGES_NEW = ("        beqz    $at, bad                # checksum: a state stored as pages (\"pXS9\") is in no sum: refused\n"
             "        addiu   $fp, $k0, PAGE+0x800\n"
             "cz_stream:\n")
CMP_OLD = ("        bne     $t0, $gp, bad           # not the length page 0 gives\n"
           "        nop\n"
           "cz_done:\n")
CMP_NEW = ("        bne     $t0, $gp, bad           # not the length page 0 gives\n"
           "        lw      $t0, CSUM($k0)          # checksum: the stream's (bit 31 flipped: the slot dropped an upload word)\n"
           "        nop\n"
           "        bne     $t0, $v0, bad           # checksum: a word of page 0, page 1 or the stream differs\n"
           "cz_done:                                # (cz_done's first instruction is the delay slot; `bad` sets a0)\n")
PAIRS = ((EQU_OLD, EQU_NEW), (P1_OLD, P1_NEW), (SAVE_OLD, SAVE_NEW), (BMP_OLD, BMP_NEW), (STORE_OLD, STORE_NEW),
         (GET_OLD, GET_NEW), (LOAD_OLD, LOAD_NEW), (PAGES_OLD, PAGES_NEW), (CMP_OLD, CMP_NEW))


def firmware_text(text):
    """A routine chain's text (after compress; readout-hold or not) with the checksum. Exact anchors."""
    if 'CS_LAST' in text or 'CS_WORD' in text:
        raise ValueError(f'{TAG}: the routine already has the step')
    for old, new in PAIRS:
        text = once(text, old, new)
    return text


# Planted faults: (old, new) on the routine with the step. Each is for the CPU bench (run_save_project_cpu.py)
# with a damaged stream or a set overflow; all must assemble here.
FAULTS = {
    # the compare is never made
    'no-compare': [("        bne     $t0, $v0, bad           # checksum: a word of page 0, page 1 or the stream differs\n",
                    "        nop                             # FAULT no-compare\n")],
    # the load's stream clear is missing: page 0's sum stays in the stream's, every load is refused
    'no-load-clear': [("        sb      $zero, CSUM($k0)        # checksum: the stream's page copies from here\n",
                       "        nop                             # FAULT no-load-clear\n")],
    # page 1 in neither sum (as revisions 1-2): a good state loads, a changed page 1 word too
    'no-page1': [("        lw      $t4, CS_P1($t1)         # checksum: page 1's sum\n",
                  "        move    $t4, $zero              # FAULT no-page1\n"),
                 ("        lw      $v0, CSUM($k0)\n", "        move    $v0, $zero              # FAULT no-page1\n")],
    # page 0 in neither sum (T = stream + page 1, compared as T - page 1): a changed page 0 word loads
    'no-page0': [("        lw      $t4, CSUM($k0)          # checksum: page 0 (word 511 = 0)\n",
                  "        move    $t4, $zero              # FAULT no-page0\n"),
                 ("        lw      $t2, CSUM($k0)\n", "        move    $t2, $zero              # FAULT no-page0\n"),
                 ("        sll     $t3, $t3, 1\n", "        nop                             # FAULT no-page0\n")],
    # page 0 goes out once, with word 511 = 0: T is not stored, every load is refused
    'single-put': [("        jal     put_page                # checksum: page 0 again, T in word 511\n"
                    "        move    $a0, $zero\n", "")],
}


def fault_text(text, name):
    for old, new in FAULTS[name]:
        text = once(text, old, new)
    return text


def chain_text(output_dir):
    """routine g (prepare_save_final_routine.py), the base of test38 (no readout-hold)."""
    import prepare_save_final_routine as final
    return list(dict(final.stages('g')).values())[-1]


def sizes(fw, path):
    """(image, extension, free bytes) or, when stage 2 does not fit, (None, None, ROM bytes - packed bytes)."""
    try:
        image, stage, extension = fw.image_parts_ext(source=path)
        return image, extension, fw.free_bytes(image, extension)
    except SystemExit as stop:
        m = re.search(r'packed stage 2 is (\d+) bytes, the ROM holds (\d+) after the stub', str(stop))
        if not m:
            raise
        return None, None, int(m[2]) - int(m[1])


def build(text, output_dir, source='chain'):
    """The routine without and with the step: sizes; the ROM files when the step fits; every fault assembles."""
    import prepare_save_rom_ext as ext
    from prepare_save_r1 import stub_bytes
    fw = ext.builder()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = {}
    for name, body in (('before', text), ('checksum', firmware_text(text))):
        path = output_dir / f'staged-{name}.S'
        path.write_text(body)
        image, extension, free = sizes(fw, path)
        rows[name] = dict(source=str(path), source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                          stub_bytes=len(stub_bytes(path)), free_bytes=free, fits=image is not None)
        if name == 'checksum' and image is not None:
            (output_dir / 'psx_save_rom40.hex').write_text(fw.rom40_hex(fw.rom1(image), fw.rom2(image)))
            (output_dir / ext.EXT_NAME).write_text(ext.ext_hex(extension))
    for name in FAULTS:
        faulty = output_dir / f'staged-fault-{name}.S'
        faulty.write_text(fault_text(firmware_text(text), name))
        sizes(fw, faulty)               # assembles (the size check aside)
    a, b = rows['before'], rows['checksum']
    report = dict(rows, chain=source, packed_bytes_added=a['free_bytes'] - b['free_bytes'], free_bytes=b['free_bytes'],
                  fits=b['fits'], instructions_added=sum(new.count('\n') - old.count('\n') for old, new in PAIRS[1:]))
    (output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    for key in ('instructions_added', 'packed_bytes_added', 'free_bytes', 'fits'):
        print(f'{TAG}: {key} = {report[key]}')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--routine', type=Path, metavar='SRC.S')
    parser.add_argument('--chain', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if bool(args.routine) == args.chain:
            parser.error('one of --routine SRC.S and --chain')
        build(args.routine.read_text() if args.routine else chain_text(args.output), args.output, str(args.routine or 'chain'))
    except ValueError as error:
        raise SystemExit(str(error))
