#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "compress": the stored pages of a state go into the save buffer as one
LZ stream, so the Pocket has less to read out ("State saved" banner) and to write back.

Design, measurements and bench results: docs/save-states-finish/compression.md.
Our own format and code (nothing taken from another core or library).

What changes (stage 2 only; chained after big build -> mdec-tap -> early-load -> size-mib):
  put_page / get_page  pages RAM_PAGE .. CD_SCRATCH-1 (every stored page: main RAM, VRAM rows,
            SPU registers, decoded samples, SPU RAM, CD FIFO) no longer go to the buffer. They
            go to and come from a "shadow" in the SDRAM above stage 2 (CZ_SHADOW, 0x80220000,
            up to 3.7 MiB of the 6 MiB that SAVE_MODE opens), at (page - RAM_PAGE) * 2 KiB.
            Pages 0, 1 and the scratch pages 2045-2047 use the buffer as before.
  save      pack_end calls cz_save first: the shadow's (s7 - RAM_PAGE) pages are compressed
            into the staging page, which the copy engine writes to buffer pages RAM_PAGE..;
            the last page is filled with zeros. Page 0 word 39 = the stream's bytes; word 38
            stays the number of stored pages (the load-check's count); the size reported to the
            Pocket (SIZE_LO/HI) is 2 + ceil(stream bytes / 2048) pages (in whole MiB with size-mib).
  load      after the magic compare and the load-check, before the remount (nothing of the
            game changed yet), cz_load fills the shadow: from the stream (new magic) or from
            the buffer's pages as they are (a state from before this step). A stream that
            points outside its data, runs past the stored pages, or does not end exactly at
            word 39 bytes and word 38 pages is refused (`bad`: the game carries on).
  magic     "pXSy" + disc (MAGIC_CZ = MAGIC_R1 ^ 0x40000000: one bit, so the load's XOR compare can strip it) for a compressed state. This
            routine loads both; a routine without the step refuses the new magic in its magic
            compare (FINISH 2, the game carries on), before it reads anything else.
The zero-page bitmap stays (page 0 words 64-127): all-zero main RAM pages and VRAM rows are
left out before the stream is made, and the load-check and the load's zero fills are unchanged.

Stream (bytes; "sequence" = token, literals, match):
  token     high nibble = literal count, low nibble = match length - 4; a nibble of 15 is
            followed by bytes that are added to it, each 255 meaning "one more byte follows"
            (after the token for the literals; after the offset for the match)
  literals  the bytes themselves
  offset    2 bytes, little-endian, 1-65535: the match starts that many bytes back in the
            output; it may overlap the bytes it produces
  The last sequence has literals only (its low nibble is 0, no offset). Worst case: no match
  at all, 1 + n/255 + n bytes: 1,801 pages grow by 15 KiB, far below the scratch pages.
Compressor: at each position with 4 bytes left, x = the 4 bytes as a word * 33 (32 bits),
x ^= x >> 13, hash = (x ^ x >> 20) & 0xFFF (4,096 words at CZ_TAB, 0x80210000, cleared first); the entry is the last position with
that hash. If it is at most 65,535 back and its 4 bytes are equal it is a match, extended to
the first byte that differs or the data's end. No match: 1 + (misses since the last match
>> 6) bytes further. Positions inside a match are not entered.

ROM: the step needs the extension ROM (docs/save-states-finish/rom-ext.md, candidate E3x): its
packed stage 2 reaches into it. build() assembles the chain with the extension loader.
Usage: prepare_save_compress.py --output OUT
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

TAB = 0x80210000
SHADOW = 0x80220000
RAM_PAGE = 2
CD_SCRATCH = 2045
PAGE_BYTES = 2048
MAGIC_BIT = 0x40000000      # "pXS9" -> "pXSy": one bit, so the load's XOR against MAGIC_R1 leaves it alone


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'compress anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


EQU_OLD = '''\
        .equ    STAGE2, 0x80200000      # stage 2's home: SDRAM 0x200000, cached (KSEG0)
'''
EQU_NEW = EQU_OLD + f'''\
        # compress (scripts/prepare_save_compress.py, docs/save-states-finish/compression.md)
        .equ    MAGIC_CZ, MAGIC_R1 ^ 0x{MAGIC_BIT:08X}   # "pXSy" + disc: the stored pages are one LZ stream
        .equ    CZ_TAB, 0x{TAB:08X}     # 4,096 words: the last position of each hash
        .equ    CZ_SHADOW, 0x{SHADOW:08X}  # the stored pages, (page - RAM_PAGE) * 2 KiB (SDRAM above stage 2)
        .equ    CZ_LEN, PAGE+4*39       # page 0 word 39: the stream's bytes
'''

SAVE_MAGIC_OLD = '''\
        lui     $t0, %hi(MAGIC_R1)
        ori     $t0, $t0, %lo(MAGIC_R1)
        addu    $t0, $t0, $t2
'''
SAVE_MAGIC_NEW = '''\
        lui     $t0, %hi(MAGIC_CZ)      # compress: the format of this save
        ori     $t0, $t0, %lo(MAGIC_CZ)
        addu    $t0, $t0, $t2
'''

LOAD_MAGIC_OLD = '''\
        xor     $s6, $t0, $t1           # R1: the format digits must match; bits 2:0 are the
'''
LOAD_MAGIC_NEW = 'cz_magic:\n' + LOAD_MAGIC_OLD + '''\
        srl     $at, $s6, 30            # compress: at = 1 for "pXSy" (a stream), 0 for "pXS9"
        andi    $at, $at, 1             # (pages); any other word still fails the compare below.
        sll     $t0, $at, 30            # at is kept up to cz_load
        xor     $s6, $s6, $t0
cz_magic_done:
'''

LOAD_CALL_OLD = '''\
        # R1 remount, before anything of the running game is changed (CD_FREEZE is off: a
'''
LOAD_CALL_NEW = '''\
        jal     cz_load                 # compress: the stored pages into the shadow, or `bad`
        nop
''' + LOAD_CALL_OLD

PACK_OLD = '''\
pack_end:
        move    $s5, $ra
        jal     get_page
        move    $a0, $zero
'''
PACK_NEW = '''\
pack_end:
        move    $s5, $ra
        jal     cz_save                 # compress: v1 = the state's pages, s1 = the stream's bytes
        nop
        jal     get_page
        move    $a0, $zero
'''
STORE_OLD = '''\
        sw      $s7, PAGE+4*38($k0)
'''
STORE_NEW = STORE_OLD + '''\
        sw      $s1, CZ_LEN($k0)        # compress
'''
# the pages reported to the Pocket: the stream's (v1), not the stored pages (s7); the routine with
# the size-mib step (whole MiB), or without it
SIZE_FORMS = (
    ('        addiu   $t0, $s7, 511\n',
     '        addiu   $t0, $v1, 511           # compress: the pages the stream took, not the stored pages\n'),
    ('        sb      $s7, SIZE_LO($k0)\n        srl     $t0, $s7, 8\n',
     '        sb      $v1, SIZE_LO($k0)       # compress: the pages the stream took, not the stored pages\n        srl     $t0, $v1, 8\n'),
)

PAGE_OLD = '''\
put_page:
        li      $t9, 1
        b       page_cmd
        nop
# a0 -> page number; save buffer -> staging page
get_page:
        li      $t9, 3
page_cmd:
'''
PAGE_NEW = '''\
put_page:
        b       cz_page
        li      $t9, 1
# a0 -> page number; save buffer -> staging page
get_page:
        li      $t9, 3
# compress: a stored page (RAM_PAGE .. CD_SCRATCH-1) lives in the shadow, not in the buffer
# (clobbers what copy_in / copy_out clobber: t0-t3, t8, t9, a1, a3; and gp, fp)
cz_page:
        addiu   $t8, $a0, -RAM_PAGE
        sltiu   $t8, $t8, CD_SCRATCH-RAM_PAGE
        beqz    $t8, page_cmd
        addiu   $t8, $a0, -RAM_PAGE
        move    $fp, $ra
        move    $gp, $s0
        sll     $t8, $t8, 11
        lui     $s0, %hi(CZ_SHADOW)
        addu    $s0, $s0, $t8
        andi    $t9, $t9, 2
        bnez    $t9, 1f
        li      $a1, 0x800
        jal     copy_in                 # staging page -> shadow
        nop
        b       2f
        nop
1:      jal     copy_out                # shadow -> staging page
        nop
2:      move    $s0, $gp
        jr      $fp
        nop

# compress, save: the shadow's s7 - RAM_PAGE pages as one stream into buffer pages RAM_PAGE..
# -> v1 = the first page after it (the state's size in pages), s1 = the stream's bytes.
# s0 position, s1 first literal not yet written, a1 last position with 4 bytes left + 1,
# gp table, t9 misses since the last match, sp / fp next byte / end of the
# staging page, v1 its buffer page; a3, at, t2, t3 return addresses
cz_save:
        move    $a3, $ra
        lui     $gp, %hi(CZ_TAB)
        move    $t0, $gp
1:      sw      $zero, 0($t0)           # no position yet (and the stream does not depend on an
        addiu   $t0, $t0, 4             # earlier request)
        andi    $t1, $t0, 0x3FFF
        bnez    $t1, 1b
        nop
        lui     $s0, %hi(CZ_SHADOW)
        addiu   $t0, $s7, -RAM_PAGE
        sll     $t0, $t0, 11
        addu    $a1, $s0, $t0
        addiu   $a1, $a1, -3
        addiu   $sp, $k0, PAGE
        addiu   $fp, $sp, 0x800
        li      $v1, RAM_PAGE
        move    $s1, $s0
        move    $t9, $zero
cz_probe:
        sltu    $t0, $s0, $a1
        beqz    $t0, cz_tail            # fewer than 4 bytes left
        lwr     $v0, 0($s0)
        lwl     $v0, 3($s0)
        addiu   $t7, $s0, 4
        sll     $t1, $v0, 5             # hash: x = word * 33; x ^= x >> 13; (x ^ x >> 20) & 0xFFF
        addu    $t1, $t1, $v0           # (no multiply: MULTU takes 7-14 clk1x here)
        srl     $t0, $t1, 13
        xor     $t1, $t1, $t0
        srl     $t0, $t1, 20
        xor     $t1, $t1, $t0
        andi    $t1, $t1, 0xFFF
        sll     $t1, $t1, 2
        addu    $t1, $t1, $gp
        lw      $t3, 0($t1)             # the last position with this hash
        sw      $s0, 0($t1)
        subu    $t6, $s0, $t3           # offset
        srl     $t0, $t6, 16
        bnez    $t0, cz_miss            # none yet, or more than 65,535 back
        nop
        lwr     $t4, 0($t3)
        lwl     $t4, 3($t3)
        addiu   $t8, $t3, 4
        bne     $t4, $v0, cz_miss
        addiu   $t2, $a1, 3             # the data's end
2:      addiu   $t0, $t7, 4             # the match's end: four bytes at a time ...
        sltu    $t0, $t2, $t0
        bnez    $t0, 6f
        lwr     $t0, 0($t8)
        lwl     $t0, 3($t8)
        lwr     $t1, 0($t7)
        lwl     $t1, 3($t7)
        addiu   $t8, $t8, 4
        beq     $t0, $t1, 2b
        addiu   $t7, $t7, 4
        addiu   $t7, $t7, -4
        addiu   $t8, $t8, -4
6:      beq     $t7, $t2, 3f            # ... then to the first byte that differs, or the data's end
        lbu     $t0, 0($t8)
        lbu     $t1, 0($t7)
        addiu   $t8, $t8, 1
        beq     $t0, $t1, 6b
        addiu   $t7, $t7, 1
        addiu   $t7, $t7, -1
3:      subu    $t4, $s0, $s1           # literals before it
        subu    $t5, $t7, $s0
        addiu   $t5, $t5, -4            # length - 4
        sltiu   $t0, $t5, 15
        bnez    $t0, 4f
        move    $t1, $t5
        li      $t1, 15
4:      jal     cz_lits
        nop
        jal     cz_out
        andi    $t0, $t6, 0xFF
        jal     cz_out
        srl     $t0, $t6, 8
        sltiu   $t0, $t5, 15
        bnez    $t0, 5f
        nop
        jal     cz_outlen
        addiu   $t1, $t5, -15
5:      move    $s0, $t7
        move    $t9, $zero
        b       cz_probe
        move    $s1, $t7
cz_miss:
        srl     $t0, $t9, 6             # no match for a while: wider steps (data that does not
        addiu   $t9, $t9, 1             # compress costs less time)
        addu    $s0, $s0, $t0
        b       cz_probe
        addiu   $s0, $s0, 1
cz_tail:
        addiu   $s0, $a1, 3             # the last sequence: literals only
        subu    $t4, $s0, $s1
        jal     cz_lits
        move    $t1, $zero
        addiu   $t0, $v1, -RAM_PAGE
        sll     $t0, $t0, 11
        addu    $s1, $sp, $t0
        subu    $s1, $s1, $fp
        addiu   $s1, $s1, 0x800         # the stream's bytes
6:      addiu   $t1, $fp, -0x800        # zeros up to the end of its last page
        beq     $sp, $t1, 7f
        nop
        jal     cz_out
        move    $t0, $zero
        b       6b
        nop
7:      jr      $a3
        nop
# token (t4 literals, t1 the match's nibble), the long count, the literals s1 .. s0
cz_lits:
        move    $t3, $ra
        sltiu   $t0, $t4, 15
        bnez    $t0, 1f
        move    $t0, $t4
        li      $t0, 15
1:      sll     $t0, $t0, 4
        jal     cz_out
        or      $t0, $t0, $t1
        sltiu   $t0, $t4, 15
        bnez    $t0, 2f
        nop
        jal     cz_outlen
        addiu   $t1, $t4, -15
2:      beq     $s1, $s0, 3f
        lbu     $t0, 0($s1)
        addiu   $s1, $s1, 1
        addiu   $sp, $sp, 1
        bne     $sp, $fp, 2b
        sb      $t0, -1($sp)
        jal     cz_flush
        nop
        b       2b
        nop
3:      jr      $t3
        nop
# t1 = what is left of a count after its nibble: bytes of 255, then the rest
cz_outlen:
        move    $t2, $ra
1:      sltiu   $t0, $t1, 255
        bnez    $t0, 2f
        nop
        li      $t0, 255
        jal     cz_out
        addiu   $t1, $t1, -255
        b       1b
        nop
2:      move    $ra, $t2
        move    $t0, $t1
# t0 = the stream's next byte; a full staging page goes to buffer page v1 (cz_flush)
cz_out:
        sb      $t0, 0($sp)
        addiu   $sp, $sp, 1
        bne     $sp, $fp, 9f
        nop
cz_flush:
        move    $at, $ra
        move    $a0, $v1
        jal     page_cmd
        li      $t9, 1
        addiu   $v1, $v1, 1
        addiu   $sp, $fp, -0x800
        move    $ra, $at
9:      jr      $ra
        nop

# compress, load (page 0 in the staging page, which comes back at the end): the stored pages
# into the shadow. at = 1: from the stream of CZ_LEN bytes in buffer pages RAM_PAGE..;
# at = 0: the buffer's pages as they are (a state from before the step).
# s0 next output byte, s1 end (word 38 pages), a2 start, gp CZ_LEN, a1 the page after the
# stream, sp / fp next byte / end of the staging page, v1 next buffer page
cz_load:
        move    $a3, $ra
        lw      $t0, PAGE+4*38($k0)
        lw      $gp, CZ_LEN($k0)
        addiu   $t0, $t0, -RAM_PAGE
        sltiu   $t1, $t0, CD_SCRATCH-RAM_PAGE+1
        beqz    $t1, bad                # more pages than the shadow's range
        sll     $t0, $t0, 11
        lui     $a2, %hi(CZ_SHADOW)
        addu    $s1, $a2, $t0
        move    $s0, $a2
        li      $v1, RAM_PAGE
        bnez    $at, cz_stream
        addiu   $fp, $k0, PAGE+0x800
1:      beq     $s0, $s1, cz_done       # pages as they are
        move    $a0, $v1
        jal     page_cmd
        li      $t9, 3
        jal     copy_in
        li      $a1, 0x800
        b       1b
        addiu   $v1, $v1, 1
cz_stream:
        addiu   $t0, $gp, 0x7FF
        srl     $t0, $t0, 11
        addiu   $a1, $t0, RAM_PAGE
        sltiu   $t0, $a1, CD_SCRATCH+1
        beqz    $t0, bad                # a stream longer than the buffer's state pages
        move    $sp, $fp                # (the first byte fetches the first page)
        li      $t3, 15
cz_seq: jal     cz_in
        nop
        srl     $t4, $t0, 4
        bne     $t4, $t3, 1f
        andi    $t5, $t0, 15
        jal     cz_inlen
        move    $t1, $t4
        move    $t4, $t1
1:      subu    $t0, $s1, $s0
        sltu    $t0, $t0, $t4
        bnez    $t0, bad                # literals past the stored pages
        addu    $t6, $s0, $t4
        beq     $s0, $t6, 3f
        nop
2:      beq     $sp, $fp, 8f
        lbu     $t0, 0($sp)             # (at the page's end this reads STATUS: no effect, not used)
        addiu   $sp, $sp, 1
        addiu   $s0, $s0, 1
        bne     $s0, $t6, 2b
        sb      $t0, -1($s0)
3:      beq     $s0, $s1, cz_end        # (only after literals: the last sequence has no match)
        nop
        jal     cz_in
        nop
        jal     cz_in
        move    $t6, $t0
        sll     $t0, $t0, 8
        bne     $t5, $t3, 4f
        or      $t6, $t6, $t0           # offset
        jal     cz_inlen
        move    $t1, $t5
        move    $t5, $t1
4:      beqz    $t6, bad
        subu    $t7, $s0, $t6
        sltu    $t0, $t7, $a2
        bnez    $t0, bad                # a match from before the first byte
        addiu   $t5, $t5, 4
        subu    $t0, $s1, $s0
        sltu    $t0, $t0, $t5
        bnez    $t0, bad                # a match past the stored pages
        addu    $t6, $s0, $t5
5:      lbu     $t0, 0($t7)
        addiu   $t7, $t7, 1
        addiu   $s0, $s0, 1
        bne     $s0, $t6, 5b
        sb      $t0, -1($s0)
        b       cz_seq
        nop
8:      jal     cz_fill
        nop
        b       2b
        nop
cz_end: addiu   $t0, $v1, -RAM_PAGE     # bytes taken: whole pages fetched, less what is left
        sll     $t0, $t0, 11
        addu    $t0, $t0, $sp
        subu    $t0, $t0, $fp
        bne     $t0, $gp, bad           # not the length page 0 gives
        nop
cz_done:
        move    $a0, $zero              # page 0 again
        move    $ra, $a3
        b       page_cmd
        li      $t9, 3
# t1 = a nibble of 15 plus its count bytes
cz_inlen:
        move    $t2, $ra
1:      jal     cz_in
        nop
        addu    $t1, $t1, $t0
        xori    $t0, $t0, 255
        beqz    $t0, 1b
        nop
        jr      $t2
        nop
# t0 = the stream's next byte
cz_in:  bne     $sp, $fp, 1f
        nop
        move    $s7, $ra
        jal     cz_fill
        nop
        move    $ra, $s7
1:      lbu     $t0, 0($sp)
        jr      $ra
        addiu   $sp, $sp, 1
# the emptied staging page is filled from buffer page v1
cz_fill:
        beq     $v1, $a1, bad           # past the stream's last page
        move    $at, $ra
        move    $a0, $v1
        jal     page_cmd
        li      $t9, 3
        addiu   $v1, $v1, 1
        addiu   $sp, $fp, -0x800
        jr      $at
        nop

# a0 -> buffer page, t9 = 1 staging page -> buffer, 3 buffer -> staging page
page_cmd:
'''

# Planted faults: (old, new) on the step's text. None is a fitted input.
FAULTS = {
    # the hash index loses its top bit: half the table. Every match is still checked against the
    # data, so states still load; only the stream differs from the model's
    'hash-index': ('        andi    $t1, $t1, 0xFFF\n        sll     $t1, $t1, 2\n',
                   '        andi    $t1, $t1, 0x7FF         # FAULT: 11 bits of the hash\n        sll     $t1, $t1, 2\n'),
    # the load makes every match one byte shorter
    'match-length': ('        bnez    $t0, bad                # a match from before the first byte\n        addiu   $t5, $t5, 4\n',
                     '        bnez    $t0, bad                # a match from before the first byte\n        addiu   $t5, $t5, 3             # FAULT: length - 3\n'),
    # page 0 gives the stream one byte too long
    'stream-length': ('        addiu   $s1, $s1, 0x800         # the stream\'s bytes\n',
                      '        addiu   $s1, $s1, 0x801         # FAULT: one byte more than written\n'),
    # the save's size register still gets the stored pages (the Pocket would copy too much; the state loads)
    'size-stored-pages': ('        addiu   $t0, $v1, 511           # compress: the pages the stream took, not the stored pages\n',
                          '        addiu   $t0, $s7, 511           # FAULT: the stored pages\n'),
    # the load does not check a match's start
    'no-offset-check': ('        bnez    $t0, bad                # a match from before the first byte\n',
                        '        nop                             # FAULT: no check\n'),
}


def firmware_text(text):
    """text: the big build's routine (R1 and the page layout of PXS6; with or without load-check,
    mdec-tap, early-load, size-mib, the rom-ext loader) -> the same with the step."""
    if 'cz_save' in text:
        raise ValueError('compress is applied already')
    text = once(text, EQU_OLD, EQU_NEW)
    text = once(text, SAVE_MAGIC_OLD, SAVE_MAGIC_NEW)
    text = once(text, LOAD_MAGIC_OLD, LOAD_MAGIC_NEW)
    text = once(text, LOAD_CALL_OLD, LOAD_CALL_NEW)
    text = once(text, PACK_OLD, PACK_NEW)
    text = once(text, STORE_OLD, STORE_NEW)
    forms = [form for form in SIZE_FORMS if form[0] in text]
    if len(forms) != 1:
        raise ValueError('compress anchor: the size report is neither the size-mib step\'s nor the plain one')
    text = once(text, *forms[0])
    text = once(text, PAGE_OLD, PAGE_NEW)
    return text


def fault_text(text, name):
    old, new = FAULTS[name]
    return once(text, old, new)


# ---- the format's model (what the routine's code must produce and accept, byte for byte) ----

def compress(data):
    """The stream cz_save writes for `data` (the stored pages)."""
    n = len(data)
    table = [None] * 4096
    out = bytearray()
    p = anchor = misses = 0

    def count(rest):
        while rest >= 255:
            out.append(255)
            rest -= 255
        out.append(rest)

    def literals(upto, nibble):
        nonlocal anchor
        lit = upto - anchor
        out.append(min(lit, 15) << 4 | nibble)
        if lit >= 15:
            count(lit - 15)
        out.extend(data[anchor:upto])
        anchor = upto

    while p + 4 <= n:
        word = data[p:p + 4]
        x = (int.from_bytes(word, 'little') * 33) & 0xFFFFFFFF
        x ^= x >> 13
        h = (x ^ x >> 20) & 0xFFF
        c = table[h]
        table[h] = p
        if c is None or p - c > 0xFFFF or data[c:c + 4] != word:
            p += 1 + (misses >> 6)
            misses += 1
            continue
        m = 4
        while p + m < n and data[c + m] == data[p + m]:
            m += 1
        literals(p, min(m - 4, 15))
        out += (p - c).to_bytes(2, 'little')
        if m - 4 >= 15:
            count(m - 4 - 15)
        p += m
        anchor = p
        misses = 0
    literals(n, 0)
    return bytes(out)


class BadStream(ValueError):
    pass


def decompress(stream, size):
    """The stored pages cz_load makes of `stream` (size bytes), or BadStream where it says `bad`."""
    out = bytearray()
    i = 0

    def take():
        nonlocal i
        if i >= (len(stream) + PAGE_BYTES - 1) // PAGE_BYTES * PAGE_BYTES:
            raise BadStream('past the last page')
        value = stream[i] if i < len(stream) else 0
        i += 1
        return value

    def count(value):
        while True:
            b = take()
            value += b
            if b != 255:
                return value

    while True:
        token = take()
        lit, ml = token >> 4, token & 15
        if lit == 15:
            lit = count(lit)
        if lit > size - len(out):
            raise BadStream('literals past the end')
        for _ in range(lit):
            out.append(take())
        if len(out) == size:
            break
        off = take()
        off |= take() << 8
        if ml == 15:
            ml = count(ml)
        ml += 4
        if off == 0 or off > len(out):
            raise BadStream('offset')
        if ml > size - len(out):
            raise BadStream('match past the end')
        for _ in range(ml):
            out.append(out[-off])
    if i != len(stream):
        raise BadStream('length')
    return bytes(out)


def base_text():
    """The chain before the step: big build -> mdec-tap -> early-load -> size-mib, with the stub's
    extension-ROM loader (prepare_save_rom_ext.py; candidate E3x has the ROM)."""
    import prepare_save_rom_ext as ext
    import prepare_save_size_mib as mib
    return ext.firmware_text(mib.firmware_text(mib.base_text()))


def build(output_dir):
    import prepare_save_rom_ext as ext
    from prepare_save_r1 import stub_bytes
    fw = ext.builder()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    base = output_dir / 'staged-base.S'                   # ... size-mib + the extension loader
    base.write_text(base_text())
    text = firmware_text(base.read_text())
    source = output_dir / 'staged-compress.S'
    source.write_text(text)
    base_image, base_stage, base_ext = fw.image_parts_ext(source=base)
    image, stage, extension = fw.image_parts_ext(source=source)      # stops if the packed stage 2 does not fit
    if stub_bytes(base) != stub_bytes(source):
        raise ValueError('the stub changed')
    (output_dir / 'psx_save_rom40.hex').write_text(fw.rom40_hex(fw.rom1(image), fw.rom2(image)))
    (output_dir / ext.EXT_NAME).write_text(ext.ext_hex(extension))
    (output_dir / 'stage2.bin').write_bytes(stage)
    packed = lambda stg: len(fw.lz_pack(stg)) + 8
    report = {'source': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'base_sha256': hashlib.sha256(base.read_bytes()).hexdigest(), 'stub_bytes': len(stub_bytes(source)),
              'stage2_bytes_before': len(base_stage), 'stage2_bytes': len(stage),
              'packed_bytes_before': packed(base_stage), 'packed_bytes': packed(stage),
              'free_bytes_before': fw.free_bytes(base_image, base_ext), 'free_bytes': fw.free_bytes(image, extension),
              'extension_bytes_used': len(extension.rstrip(b'\0'))}
    for fault in FAULTS:
        faulty = output_dir / f'staged-compress-fault-{fault}.S'
        faulty.write_text(fault_text(text, fault))
        fw.image_parts_ext(source=faulty)
    (output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    for key, value in report.items():
        print(f'{key}: {value}')
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--output', type=Path, required=True)
    build(ap.parse_args().output)
