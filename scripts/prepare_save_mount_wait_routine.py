#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "mount-wait": the remount wait (disc_mount, DISC_POLLS) from about 4.4 s to about 35 s.

test5 (2026-11-13): 1 wake in 4 went to the BIOS shell (no disc), then the logo and a fresh game. With
wake-hold the routine runs during the BIOS start; a wake has no boot name and an unmounted disc, so it
answers early and waits for the state's disc to mount and settle. The wake's first mount (SD: cue/JSON,
the image, the track table) can take longer than 4.4 s; disc_mount then refused (v0 2, path 70) and the
BIOS found no disc. 0x04000000 looks (at least 18 clk1x each: 35 s or more). Every remount wait gets the
bound (a slow or stuck in-game switch freezes longer before it is refused; with the cue reader a disc
that cannot open settles at once with the wrong-disc bit). Same size (an lui immediate).
Bench forms (FAULTS, never a ROM): the bound scaled down so the CPU bench can reach it; scenario
'wake slow mount' (save_early_load_bench.py) settles the boot mount 1,000,000 clk1x after the load:
bench-short (0x4000 looks, under it) must refuse, bench-long (0x20000 looks, past it) must load.
"""
TAG = 'mount-wait'
OLD = "        .equ    DISC_POLLS, 0x00800000  # remount wait: at least 18 clk1x a look, 4.4 s or more\n"
NEW = ("        .equ    DISC_POLLS, 0x04000000  # remount wait: at least 18 clk1x a look, 35 s or more "
       "(mount-wait: a wake's first mount can take longer than 4.4 s)\n")
FAULTS = {
    'bench-short': [(NEW, NEW.replace('0x04000000', '0x00004000'))],
    'bench-long': [(NEW, NEW.replace('0x04000000', '0x00020000'))],
    'old-bound': [(NEW, OLD)],
}


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} anchor: expected one match, found {text.count(old)}: {old[:80]!r}')
    return text.replace(old, new)


def firmware_text(text):
    if '(mount-wait:' in text:
        raise ValueError(f'{TAG}: the routine already has the step')
    return once(text, OLD, NEW)


def fault_text(text, name):
    for old, new in FAULTS[name]:
        text = once(text, old, new)
    return text
