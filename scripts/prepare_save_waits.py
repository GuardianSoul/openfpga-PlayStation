#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "waits": bounded waits and range checks (stage 2 only).

Applies after load-check (it extends that check) on the big-build routine, alone or with the
MDEC tap and the early-load step (routine c2); the rom-ext loader and compress apply after it.
Parts (PARTS; all by default, a subset for a ROM that has no room for all):
  retry       retry give-up: the retry counter is compared as "at least RETRY_MAX", not "equal":
              a counter above the limit (a staging page word that is not this session's) gives
              up at once instead of retrying for 2^32 rounds.
  cd-count    cd_wait: the poll counter a2 is tested as "> 0". It could pass zero when an INT1
              came on the last poll (the wait went on at 3: with a2 = 0, then -1, ...: unbounded).
  page        the copy engine's busy bit (every page copy), WAIT_LOOPS polls
  ss          the SPU port's busy bit, WAIT_LOOPS polls
  quiet-poll  spu-quiet's poll before an SPU RAM page command, WAIT_LOOPS polls
  cd-drain    cd_wait: a response FIFO that never empties (counted in a2, the wait's budget)
  scratchpad  wait_dead of a save puts the game's scratchpad back (page 1) when the page bitmap
              has taken its place (s7 below CD_SCRATCH; the save sets s7 before its first copy)
  fifo-clamp  cd_give: the CD data FIFO's byte count of a state is taken modulo 4096 (one
              instruction). A damaged count then gives back at most 4,095 bytes (the two pages
              hold 4,092; the last three are the copy engine's status and page registers, read
              without effect) instead of walking up to 2 GiB of bus reads with the game frozen.
              The state loads with a wrong FIFO content, as any damaged byte of a state does.
  fifo-check  the CD data FIFO's byte count of a state (word 0 of its last stored page) is range
              checked before anything of the game is changed (after compress has filled its
              shadow, before the early answer and the remount): more than 2048 + 2044 bytes
              (what the two FIFO pages hold) goes to `bad`.
WAIT_LOOPS is 0x00100000 (about 10 clk1x a poll, so about 10 million clk1x, 0.3 s; a page copy of
a healthy device takes a few thousand clk1x).
A wait that runs out goes to wait_dead: the game's registers come back from the buffer (a load:
the scratch page, a save: page 0), FINISH 2 (unless the load was answered early), the normal
exit. If the copy engine itself is dead (a second timeout, k1 bit 1), the error exit with the
staging page as it is: the registers of the game are then the staging page's, which are the
game's only while no page copy has replaced them (the first copy of a request).
k1: 4 = a load (the stub), bit 0 = answered early (early-load), bit 1 = a wait has run out.
Bounded already, not touched: the quiet-point looks, mdec_quiet (MDEC_LOOKS), the MDEC table tap
(129 passes), spu_wait (0x2000), disc_mount (DISC_POLLS), cd_ready's drain (0x400 waits of 0x100
polls) and rounds (READY_ROUNDS; each command 0x40000 polls, GetID's second response 0x100000).
Not done: polls of GPUSTAT before GPU writes (the writes wait in the bus's own GPU stall,
which the routine cannot bound without changing what is sent), the results of the CD
setup commands.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'waits anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


EQU_ANCHOR = '        .equ    QUIET_POLLS, 2048       # GPU / DMA looks per retry (~0.8 ms from the cache)\n'
EQU = '''\
        .equ    WAIT_LOOPS, 0x00100000  # waits: polls before a device counts as dead (about 10 clk1x
                                        # each: 0.3 s; a healthy page copy takes a few thousand clk1x)
'''

RETRY_OLD = '''\
        lw      $t0, RETRIES($k0)
        li      $t1, RETRY_MAX+1
        addiu   $t0, $t0, 1
        beq     $t0, $t1, give_up       # no quiet point found in time: an error
        sw      $t0, RETRIES($k0)       # (restore clears it again then)
'''
RETRY_NEW = '''\
        lw      $t0, RETRIES($k0)
        nop
        sltiu   $t1, $t0, RETRY_MAX     # waits: "at least", so a count above the limit ends too
        addiu   $t0, $t0, 1
        beqz    $t1, give_up            # no quiet point found in time: an error
        sw      $t0, RETRIES($k0)       # (restore clears it again then)
'''

PAGE_OLD = '''\
        sb      $t9, STATUS($k0)
        TIMEOUT_START
3:      TIMEOUT_STEP
        lbu     $t9, STATUS($k0)
        nop
        andi    $t9, $t9, 1
        bnez    $t9, 3b
        nop
'''
PAGE_NEW = '''\
        sb      $t9, STATUS($k0)
        TIMEOUT_START
        lui     $t8, WAIT_LOOPS >> 16   # waits: the copy engine's bound
3:      TIMEOUT_STEP
        lbu     $t9, STATUS($k0)
        addiu   $t8, $t8, -1
        beqz    $t8, wait_dead          # waits: the copy never ended
        andi    $t9, $t9, 1
        bnez    $t9, 3b
        nop
'''

SS_OLD = '''\
        sb      $a0, SS_CMD($k0)
        TIMEOUT_START
15:     TIMEOUT_STEP
        lbu     $t0, SS_CMD($k0)
        nop
        andi    $t0, $t0, 1
        bnez    $t0, 15b
        nop
'''
SS_NEW = '''\
        sb      $a0, SS_CMD($k0)
        TIMEOUT_START
        lui     $t6, WAIT_LOOPS >> 16   # waits: the SPU port's bound
15:     TIMEOUT_STEP
        lbu     $t0, SS_CMD($k0)
        addiu   $t6, $t6, -1
        beqz    $t6, wait_dead          # waits: the port never answered
        andi    $t0, $t0, 1
        bnez    $t0, 15b
        nop
'''

# spu-quiet's poll (label 93). v1 is free there: its users are the DIAG marks (set at each use)
# and compress (cz_save / cz_load, which call no SPU RAM page command).
QUIET_OLD = '''\
        b       93f
        li      $t9, 7
# a0 -> SPU RAM page; staging page -> SPU RAM
spu_ram_put:
        li      $t9, 5
93:     lbu     $t8, SPU_HOLD($k0)      # [0]: the SPU is quiet (held, voice loop idle)
        nop
        andi    $t8, $t8, 1
        beqz    $t8, 93b
        nop
'''
QUIET_NEW = '''\
        b       98f
        li      $t9, 7
# a0 -> SPU RAM page; staging page -> SPU RAM
spu_ram_put:
        li      $t9, 5
98:     lui     $v1, WAIT_LOOPS >> 16   # waits: the quiet poll's bound
93:     lbu     $t8, SPU_HOLD($k0)      # [0]: the SPU is quiet (held, voice loop idle)
        addiu   $v1, $v1, -1
        beqz    $v1, wait_dead          # waits: the SPU never got quiet
        andi    $t8, $t8, 1
        beqz    $t8, 93b
        nop
'''

CD_COUNT_OLD = '''\
        bnez    $t0, 4f
        nop
        bnez    $a2, 3b
        nop
        jr      $ra
        li      $v0, -1
4:      lbu     $v0, 0x1801($t9)
'''
CD_COUNT_NEW = '''\
        bnez    $t0, 4f
        nop
        bgtz    $a2, 3b                 # waits: "> 0" (after an INT1 on the last poll a2 is 0 here)
        nop
96:     jr      $ra
        li      $v0, -1
4:      lbu     $v0, 0x1801($t9)
'''
CD_DRAIN_OLD = '''\
5:      lbu     $t1, 0x1800($t9)
        nop
        andi    $t1, $t1, 0x20
        beqz    $t1, 6f
        nop
        b       5b
        lbu     $t1, 0x1801($t9)        # the rest of the response
'''
CD_DRAIN_NEW = '''\
5:      lbu     $t1, 0x1800($t9)
        addiu   $a2, $a2, -1            # waits: the response bytes count against the budget
        andi    $t1, $t1, 0x20
        beqz    $t1, 6f
        nop
        blez    $a2, 96b                # waits: a response FIFO that never empties: as no answer
        nop
        b       5b
        lbu     $t1, 0x1801($t9)        # the rest of the response
'''

# fifo-check: after the R1 remount comment (compress puts its cz_load call before that comment,
# so the last stored page is read from wherever get_page finds it: the buffer, or the shadow)
CHECK_OLD = '''\
        # R1 remount, before anything of the running game is changed (CD_FREEZE is off: a
        # load never sets it). On a wake this is also the wait for the boot-time mount.
'''
CHECK_NEW = CHECK_OLD + '''\
        # waits: the CD data FIFO's byte count, word 0 of the last stored page (cd_pages puts
        # the two FIFO pages last), must fit the two pages: 2048 + 2044 bytes
        lw      $t0, PAGE+4*38($k0)
        jal     get_page
        addiu   $a0, $t0, -1
        lw      $t3, PAGE($k0)
        jal     get_page                # page 0 back into the staging page
        move    $a0, $zero
        sltiu   $t3, $t3, 0xFFD
        beqz    $t3, bad                # waits: a FIFO count no state can have
        nop
'''

CLAMP_OLD = '''\
        lw      $s6, PAGE($k0)          # the count
        jal     get_page
        move    $a0, $s0
'''
CLAMP_NEW = CLAMP_OLD + '''\
        andi    $s6, $s6, 0xFFF         # waits: a count no state can have ends within the two pages
'''

# scratchpad: s7 is set before the save's first page copy, so wait_dead can tell from it whether
# the page bitmap has taken the scratchpad's place (s7 = RAM_PAGE and up) or not (CD_SCRATCH and up)
S7_OLD = '''\
        jal     put_page
        move    $a0, $zero
        li      $s7, CD_SCRATCH         # the CD data FIFO's bytes (they need the CD bus) ...
'''
S7_NEW = '''\
        li      $s7, CD_SCRATCH         # the CD data FIFO's bytes (they need the CD bus) ...
        jal     put_page                # (waits: s7 before the first copy, wait_dead reads it)
        move    $a0, $zero
'''

DEAD_OLD = '''\
bad:
        jal     get_page
        li      $a0, SCRATCH
give_up:
'''
DEAD_HEAD = '''\
# waits: a device did not answer within WAIT_LOOPS polls. The game's registers back from
# the buffer (a load keeps them in the scratch page: `bad`; a save in page 0), then the error
# exit. If the copy engine is the dead one (this is the second time: k1 bit 1), the error exit
# with the staging page as it is.
wait_dead:
        andi    $t0, $k1, 2
        bnez    $t0, give_up
        ori     $k1, $k1, 2
        andi    $t0, $k1, 4
        bnez    $t0, bad                # a load
'''
DEAD_SCRATCHPAD = '''\
        sltiu   $t0, $s7, CD_SCRATCH    # a save: is the page bitmap in the scratchpad?
        beqz    $t0, 97f
        move    $a0, $zero
        jal     get_page                # waits: the game's scratchpad back from page 1
        li      $a0, CPU_PAGE
        lui     $s0, 0x1F80
        jal     copy_in
        li      $a1, 0x400
        b       97f
        move    $a0, $zero
'''
DEAD_PLAIN = '''\
        nop
        b       97f
        move    $a0, $zero
'''
DEAD_TAIL = '''\
bad:
        li      $a0, SCRATCH
97:     jal     get_page
        nop
give_up:
'''

PARTS = ('retry', 'cd-count', 'page', 'ss', 'quiet-poll', 'cd-drain', 'scratchpad', 'fifo-clamp', 'fifo-check')
DEAD_USERS = ('page', 'ss', 'quiet-poll')

# Deliberate faults for the bench (never a fitted input)
FAULTS = {
    'retry-equality': ('        sltiu   $t1, $t0, RETRY_MAX     # waits: "at least", so a count above the limit ends too\n',
                       '        xori    $t1, $t0, RETRY_MAX     # FAULT: gives up on equality only\n'),
    'page-wait-unbounded': ('        beqz    $t8, wait_dead          # waits: the copy never ended\n',
                            '        nop                             # FAULT: no bound\n'),
    'ss-wait-unbounded': ('        beqz    $t6, wait_dead          # waits: the port never answered\n',
                          '        nop                             # FAULT: no bound\n'),
    'quiet-poll-unbounded': ('        beqz    $v1, wait_dead          # waits: the SPU never got quiet\n',
                             '        nop                             # FAULT: no bound\n'),
    'fifo-count-unchecked': ('        beqz    $t3, bad                # waits: a FIFO count no state can have\n',
                             '        nop                             # FAULT: count not checked\n'),
    'fifo-count-unclamped': ('        andi    $s6, $s6, 0xFFF         # waits: a count no state can have ends within the two pages\n',
                             '        nop                             # FAULT: count not clamped\n'),
    'cd-drain-unbounded': ('        blez    $a2, 96b                # waits: a response FIFO that never empties: as no answer\n',
                           '        nop                             # FAULT: no bound\n'),
    # wait_dead of a save leaves the page bitmap in the game's scratchpad
    'scratchpad-not-restored': ('        beqz    $t0, 97f\n        move    $a0, $zero\n        jal     get_page                # waits: the game\'s scratchpad back from page 1\n',
                                '        b       97f                     # FAULT: the scratchpad is not put back\n        move    $a0, $zero\n        jal     get_page                # waits: the game\'s scratchpad back from page 1\n'),
}
FAULT_PART = {'retry-equality': 'retry', 'page-wait-unbounded': 'page', 'ss-wait-unbounded': 'ss', 'quiet-poll-unbounded': 'quiet-poll',
              'fifo-count-unchecked': 'fifo-check', 'fifo-count-unclamped': 'fifo-clamp', 'cd-drain-unbounded': 'cd-drain', 'scratchpad-not-restored': 'scratchpad'}

# BENCH-ONLY: the bound as 16-bit immediates, so a dead-device run ends in minutes of simulation
BENCH_LOOPS = 4096
BOUNDS = (('$t8', "the copy engine's bound"), ('$t6', "the SPU port's bound"), ('$v1', "the quiet poll's bound"))


def bench_bound(text):
    """BENCH-ONLY override of the lui WAIT_LOOPS loads (same instruction count)."""
    found = 0
    for reg, what in BOUNDS:
        old = f'lui     {reg}, WAIT_LOOPS >> 16   # waits: {what}\n'
        if old in text:
            found += 1
            text = once(text, old, f'li      {reg}, {BENCH_LOOPS}            # BENCH-ONLY OVERRIDE (the routine has lui {reg}, WAIT_LOOPS >> 16)\n')
    if not found:
        raise ValueError('waits: no bound to override')
    return text


def firmware_text(text, parts=PARTS):
    import re
    if 'wait_dead' in text:
        raise ValueError('waits is applied already')
    if 'load-check' not in text:
        raise ValueError('waits applies after load-check')
    if 'cz_save' in text or 'rom_ext' in text:
        raise ValueError('waits applies before the rom-ext loader and compress')
    for part in parts:
        if part not in PARTS:
            raise ValueError(f'waits: unknown part {part}')
    if re.search(r'^9[678]:|[ ,]9[678][fb]\b', text, flags=re.M):
        raise ValueError('numeric label 96, 97 or 98 is in use')
    if 'cd-drain' in parts and 'cd-count' not in parts:
        raise ValueError('waits: cd-drain needs cd-count (label 96)')
    dead = any(p in parts for p in DEAD_USERS)
    if 'scratchpad' in parts and not dead:
        raise ValueError('waits: scratchpad needs a bounded wait')
    table = {'retry': (RETRY_OLD, RETRY_NEW), 'cd-count': (CD_COUNT_OLD, CD_COUNT_NEW), 'page': (PAGE_OLD, PAGE_NEW), 'ss': (SS_OLD, SS_NEW),
             'quiet-poll': (QUIET_OLD, QUIET_NEW), 'cd-drain': (CD_DRAIN_OLD, CD_DRAIN_NEW), 'scratchpad': (S7_OLD, S7_NEW),
             'fifo-clamp': (CLAMP_OLD, CLAMP_NEW), 'fifo-check': (CHECK_OLD, CHECK_NEW)}
    if dead:
        text = once(text, EQU_ANCHOR, EQU_ANCHOR + EQU)
        text = once(text, DEAD_OLD, DEAD_HEAD + (DEAD_SCRATCHPAD if 'scratchpad' in parts else DEAD_PLAIN) + DEAD_TAIL)
    for part in PARTS:
        if part in parts:
            text = once(text, *table[part])
    return text


def fault_text(text, name):
    return once(text, *FAULTS[name])


if __name__ == '__main__':
    raise SystemExit('the routine with this step is built by prepare_save_final_routine.py')
