#!/usr/bin/env python3
"""Decode the launch timestamps (psx_boot_timing) from a saved boot log.

A timing build's boot-log slot holds N words (16 for timing1, 32 for
timing2): "TIME", the tick counter when the Pocket saved the slot, then the
clk_74a tick (74.25 MHz, from the configured bitstream) at the first rise of
each event, 0 for one that never happened. On hardware the file came out
one word early, wrapping: file word k is core word (k + 1) mod N, so "TIME"
is file word N-1 (timing1, 2026-10-22); the block is aligned by it
(docs/pocket-launch-timing.md).
"""
import argparse
from pathlib import Path
import struct

MAGIC = 0x54494d45
CLOCK = 74.25e6
# psx_boot_timing event inputs 0..13, in the order core_top wires them.
EVENTS = (
    'Chip32 writes the slot table',
    'BIOS loadf starts (data slot write request)',
    'BIOS loaded (psx_bios_loader ready)',
    'Chip32 sets the region (after the BIOS loadf)',
    'memory card data arrives',
    'Reset Exit (host 0x4002)',
    'APF all slots complete',
    'BIOS patched and ready',
    'cue reader mounted (or failed)',
    'disc reached cd_top (disc_settled)',
    'boot disc gate open',
    'CPU released',
    'first picture after CPU release',
    'first nonzero pixel at all',
    # timing2: the cue reader's APF target commands
    'command #1 starts', 'command #2 starts', 'command #4 starts', 'command #8 starts',
    'command #16 starts', 'command #32 starts', 'command #48 starts', 'command #64 starts',
    'command #128 starts', 'first command error', 'second command error', 'third command error',
    'first command ends OK', 'first command over 7 ms', 'first command after 113 ms idle',
    'first command after the mount',
)


def decode(data):
    words = list(struct.unpack(f'<{len(data) // 4}I', data[:len(data) // 4 * 4]))
    at = next((k for k, w in enumerate(words) if w == MAGIC), None)
    if at is None: raise SystemExit('no TIME block in this file (not a timing build?)')
    n = at + 1                                      # block size: TIME is its last file word
    if n not in (16, 32): raise SystemExit(f'TIME at file word {at}: unexpected layout')
    block = [words[(at + j) % n] for j in range(n)]
    return block[1], block[2:2 + min(len(EVENTS), n - 2)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    args = parser.parse_args()
    now, stamps = decode(args.log.read_bytes())
    ms = lambda t: t / CLOCK * 1000
    print(f'saved at {ms(now) / 1000:.2f} s after the bitstream started')
    rows = sorted((t, name) for t, name in zip(stamps, EVENTS) if t)
    prev = None
    for t, name in rows:
        step = f'  (+{ms(t - prev):7.1f})' if prev is not None else ''
        print(f'{ms(t):9.1f} ms  {name}{step}')
        prev = t
    for t, name in zip(stamps, EVENTS):
        if not t: print(f'      never  {name}')
    get = dict(zip(EVENTS, stamps + [0] * len(EVENTS)))
    def span(a, b, label):
        if get[a] and get[b]: print(f'{label}: {ms(get[b] - get[a]):.1f} ms')
    print()
    span(EVENTS[0], EVENTS[1], 'Chip32 before the bar (table, BIOS checks)')
    span(EVENTS[1], EVENTS[2], 'the bar (BIOS loadf)')
    span(EVENTS[2], EVENTS[5], 'BIOS loaded to Reset Exit (card load, Chip32 exit)')
    span(EVENTS[5], EVENTS[8], 'Reset Exit to cue mount')
    span(EVENTS[5], EVENTS[11], 'Reset Exit to CPU release')
    span(EVENTS[11], EVENTS[12], 'CPU release to first picture')
    span(EVENTS[5], EVENTS[12], 'Reset Exit to first picture')


if __name__ == '__main__': main()
