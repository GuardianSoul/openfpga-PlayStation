#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "mount-wait-wake" (0.5.10; replaces the global mount-wait of test6): the remount wait
is long only when the running machine has no boot name (k1 bit 3: a wake or the BIOS screens), where the
first mount can take longer than 4.4 s (test5: a wake to the BIOS shell, then a fresh game). In-game
remounts keep DISC_POLLS (0x00800000 looks, 4.4 s or more), so a stuck in-game swap freezes no longer.
  k1 bit 3 set: (8 * 2 + 1) << 23 = 0x08800000 looks (at least 18 clk1x each: 70 s or more)
  clear:        1 << 23 = 0x00800000 = DISC_POLLS
Three instructions more (12 bytes; routine-rel2 has 82 free). disc_mount uses t0-t2 only, as before.
Bench forms (FAULTS, never a ROM), scaled so the CPU bench can reach the short bound (shift 14: 16,384
looks without bit 3, 278,528 with it); scenario 'wake slow mount' (the boot mount settles 1,000,000 clk1x
after the load, no boot name): 'bench' must load; 'bench-no-wake' (the bit not selecting the long bound)
must refuse.
"""
TAG = 'mount-wait-wake'
OLD = "        lui     $t2, DISC_POLLS >> 16\n"
NEW = '''\
        andi    $t2, $k1, 8             # mount-wait-wake: k1 bit 3 (no boot name: a wake, the BIOS screens)
        sll     $t2, $t2, 1
        addiu   $t2, $t2, 1
        sll     $t2, $t2, 23            # 0x08800000 looks (70 s or more) with it, else DISC_POLLS (4.4 s)
'''
BENCH = NEW.replace('sll     $t2, $t2, 23', 'sll     $t2, $t2, 14')
FAULTS = {
    'bench': [(NEW, BENCH)],
    'bench-no-wake': [(NEW, BENCH.replace('andi    $t2, $k1, 8 ', 'andi    $t2, $k1, 0 '))],
    'no-wake': [(NEW, NEW.replace('andi    $t2, $k1, 8 ', 'andi    $t2, $k1, 0 '))],
}


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} anchor: expected one match, found {text.count(old)}: {old[:80]!r}')
    return text.replace(old, new)


def firmware_text(text):
    if '# mount-wait-wake:' in text:
        raise ValueError(f'{TAG}: the routine already has the step')
    if '(mount-wait:' in text:
        raise ValueError(f'{TAG}: not with the global mount-wait step')
    return once(text, OLD, NEW)


def fault_text(text, name):
    for old, new in FAULTS[name]:
        text = once(text, old, new)
    return text
