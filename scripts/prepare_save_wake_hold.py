#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""RTL step "wake-hold" (psx_save_exp1.sv, after save-trap-timeout): the trap's time-out counts only while
the core runs, not while the Pocket holds it in reset.

Found 2026-11-13 (0.5.9: a wake shows the PlayStation logo and starts the game fresh, Crash and SOTN,
intermittent; in-game Memories load). A wake's load arrives while the Pocket holds the core in reset:
the device answers it OK at once (held_load_74) and raises the trap for the CPU after the reset. The
save-trap-timeout counter (`trap_age`) stopped only for a pause, so a reset held longer than 2^27 clk2x
(1.98 s) after the load ended the trap before the CPU ran; the request ended with an error the Pocket
never sees (it was told OK), and the BIOS booted the game. The step adds the synchronised core reset
(`s2x_core[1]`, already there for the core reset edge) to the hold condition (part 1, in test4).
Part 2 (test4 wakes restarted 1 in 3): the CPU takes the trap only with interrupts enabled (cpu.vhd: the
trap counts as an IRQ under SR IEc), and after the reset the BIOS runs without them, at times longer
than 1.98 s; a load that arrived while the core was held (wake_req) therefore has no time-out at all.
The Pocket was told OK for it at once, so no Pocket wait can hang on it. Every other request keeps the
1.98 s. Applies to a device with or without part 1.
Bench: run_save_wake_hold.py (tb_save_wake_hold.sv: the trap-timeout bench with directed case W, a
load held through a reset longer than the time-out). Faults below must fail.
Usage: prepare_save_wake_hold.py SOURCE --output OUTPUT   (a pinned psx_save_exp1.sv with save-trap-timeout)
"""
import argparse
from pathlib import Path

TAG = 'wake-hold'
OLD = "trap_age <= trap ? trap_age + {{TRAP_AGE_BITS{1'b0}}, !core_paused} : 0;"
GATE1 = ("trap_age <= trap ? trap_age + {{TRAP_AGE_BITS{1'b0}}, !core_paused && !s2x_core[1]} : 0;"
         "   // wake-hold: not while the core is held in reset")
PART2 = ("trap_age <= trap ? trap_age + {{TRAP_AGE_BITS{1'b0}}, !core_paused && !s2x_core[1] && !wake_req} : 0;"
         "   // wake-hold: not while the core is held in reset, never for a wake's load")   # test5 first fit
# merged (test5, one LAB over): a held load is a wake's load and exempt, so part 1's reset term is dropped;
# the one difference: a save requested while the core is held times out as in 0.5.9 (an error to the Pocket)
NEW = ("trap_age <= trap ? trap_age + {{TRAP_AGE_BITS{1'b0}}, !core_paused && !wake_req} : 0;"
       "   // wake-hold: never for a wake's load (it came while the core was held in reset)")
# part 2 (test4, 2026-11-13): a wake's load (it arrived with the core held in reset, answered OK at once) has
# no time-out: the CPU takes the trap only with interrupts enabled, and after the reset the BIOS runs
# without them for a time that varies (more than 1.98 s at times: the white Sony logo, then a fresh game)
REQ_OLD = "                want_load <= req_load_74; want_save <= !req_load_74; trap <= 1'b1;\n"
REQ_NEW = REQ_OLD + "                wake_req <= req_load_74 && s2x_core[1];   // wake-hold: a load that came in reset (a wake)\n"
REG_OLD = "    reg trap_tout = 1'b0;\n"
REG_NEW = REG_OLD + "    reg wake_req = 1'b0;   // wake-hold: the running request is a wake's load (no time-out)\n"
RST_OLD = "            trap_age <= 0; trap_tout <= 1'b0;   // save-trap-timeout\n"
RST_NEW = RST_OLD + "            wake_req <= 1'b0;   // wake-hold\n"
PARTS = [(REG_OLD, REG_NEW), (RST_OLD, RST_NEW), (REQ_OLD, REQ_NEW)]
FAULTS = {
    # no exemption: the 0.5.9 behaviour (cases W and W2)
    'no-exempt': [(NEW, OLD)],
    # the exemption for any load, also one that came while the core ran (case F)
    'exempt-any-load': [(REQ_NEW, REQ_OLD + "                wake_req <= req_load_74;   // wake-hold\n")],
    # the exemption inverted: only a wake's load times out, a running core's untaken trap never ends (case A)
    'exempt-inverted': [(NEW, NEW.replace('!wake_req', 'wake_req'))],
    # part 1's reset gate in place of the exemption (test4): the reset ends, the BIOS runs (case W2)
    'gate-only': [(NEW, GATE1)],
}


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} anchor: expected one match, found {text.count(old)}: {old[:80]!r}')
    return text.replace(old, new)


def device_text(text):
    """psx_save_exp1.sv after save-trap-timeout, bare, with test4's gate or with test5's first form -> the merged step."""
    if NEW in text:
        raise ValueError(f'{TAG}: the device already has the step')
    if PART2 in text:   # test5's first fit: only the gate changes
        return once(text, PART2, NEW)
    if 's2x_core' not in text:
        raise ValueError(f'{TAG}: needs save-core-reset (s2x_core)')
    text = once(text, next(x for x in (PART2, GATE1, OLD) if x in text), NEW)
    for old, new in PARTS:
        text = once(text, old, new)
    return text


def device_text_part2(text):
    """psx_save_exp1.sv bare or with test4's gate -> test5's first form (PART2: reset gate and wake exemption).
    This is the form in T5 s3 = 0.6.0-test9 = 0.5.10 (`prepare_frozen_text.py --func device_text_part2`);
    the merged form above is T5b, which did not fit."""
    if PART2 in text or NEW in text:
        raise ValueError(f'{TAG}: the device already has the step')
    if 's2x_core' not in text:
        raise ValueError(f'{TAG}: needs save-core-reset (s2x_core)')
    text = once(text, next(x for x in (GATE1, OLD) if x in text), PART2)
    for old, new in PARTS:
        text = once(text, old, new)
    return text


def fault_text(text, name):
    for old, new in FAULTS[name]:
        text = once(text, old, new)
    return text


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('source', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    args.output.write_text(device_text(args.source.read_text()))


if __name__ == '__main__':
    main()
