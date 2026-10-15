#!/usr/bin/env python3
"""Decode a PSXEarly boot-log.bin (rtl/pocket/psx_boot_trace.sv layout).

The bridge returns each word one read late, so word n of the trace is word
n+1 of the file. Entries are runs of consecutive same-kind main-RAM requests.
"""
import argparse
from pathlib import Path
import struct

HEADER = ('status', 'main-RAM requests', 'main-RAM completions', 'last main-RAM address',
          'VRAM commands', 'VRAM read beats', 'PSX vblanks', 'PSRAM async accesses')


def run(word):
    return word >> 26, 'W' if word >> 25 & 1 else 'R', word & 0x1ffffff


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--last', type=int, default=80)
    parser.add_argument('--data', type=int, default=64, help='data-log pairs to print')
    parser.add_argument('--bios', type=Path, help='Check logged kernel-copy writes against this BIOS')
    parser.add_argument('--cache', action='store_true', help='ROM-log words hold cache fills (prepare --cache-trace): check kernel-code fills against the BIOS')
    args = parser.parse_args()
    raw = args.log.read_bytes()
    count = len(raw) // 4
    file_words = struct.unpack(f'<{count}I', raw[:count * 4])
    words = [0] + list(file_words[:-1])           # undo the one-word read lag
    for name, value in zip(HEADER[1:], words[1:8]):
        print(f'{name:24} {value:#010x} ({value})')
    ring, total = words[8], words[9]
    runs = words[10] if count > 4096 else None
    print(f'traced requests: {total}' + (f', runs: {runs}' if runs is not None else ''))
    first_n = min(runs if runs is not None else total, 2032)
    recent_n = min(runs if runs is not None else total, 2048)
    first = [run(w) for w in words[16:16 + first_n]]
    recent = [run(words[2048 + (ring - recent_n + i) % 2048]) for i in range(recent_n)]
    for title, entries in (('first runs', first), ('most recent runs', recent[-args.last:])):
        print(f'\n{title}:')
        for length, kind, address in entries:
            print(f'  {kind} {address:#09x} x{length}{"+" if length == 63 else ""}')
    if count > 4096 and args.bios:
        rom = struct.unpack('<131072I', args.bios.read_bytes())
        copied = [(words[4096 + 2 * i] & 0x1ffffff, words[4097 + 2 * i]) for i in range(1024)
                  if words[4096 + 2 * i] >> 31 and 0x500 <= words[4096 + 2 * i] & 0x1ffffff < 0x10000]
        # The kernel is copied from BIOS offset 0x10000 to RAM 0x500.
        bad = [(a, d, rom[(a + 0xfb00) // 4]) for a, d in copied if rom[(a + 0xfb00) // 4] != d]
        print(f'\nkernel copy check: {len(copied) - len(bad)} of {len(copied)} words match the BIOS')
        for a, d, e in bad[:16]:
            print(f'  {a:#07x} wrote {d:#010x} expected {e:#010x} (bits {d ^ e:#010x})')
    if count > 6144 and args.bios and args.cache:
        rom = struct.unpack('<131072I', args.bios.read_bytes())
        fills = [(words[6144 + 2 * i], words[6145 + 2 * i]) for i in range(512) if words[6144 + 2 * i]]
        bad = []
        for hi, data in fills:
            lanes, request = hi >> 28, hi & 0x1ffffff
            lane = (lanes & -lanes).bit_length() - 1
            address = (request & ~0xf) + 4 * lane
            if 0x500 <= address < 0x6000 and rom[(address + 0xfb00) // 4] != data:
                bad.append((request, lanes, address, data, rom[(address + 0xfb00) // 4]))
        code = sum(1 for hi, _ in fills if 0x500 <= (hi & 0x1fffff0) < 0x6000)
        print(f'\ncache fill check: {len(fills)} fills, {code} in kernel code 0x500-0x6000, {len(bad)} differ from the kernel image')
        for r, l, a, d, e in bad[:24]:
            print(f'  request {r:#07x} lanes {l:04b} word {a:#07x} filled {d:#010x} kernel {e:#010x}')
    elif count > 6144 and args.bios:
        rom = rom if 'rom' in locals() else struct.unpack('<131072I', args.bios.read_bytes())
        fetched = [(words[6144 + 2 * i] & 0x1ffffff, words[6145 + 2 * i]) for i in range(512)
                   if words[6144 + 2 * i] or words[6145 + 2 * i]]
        bad = [(a, d, rom[(a - 0x800000) // 4]) for a, d in fetched if rom[(a - 0x800000) // 4] != d]
        print(f'\nBIOS read check: {len(fetched) - len(bad)} of {len(fetched)} sampled reads match the BIOS')
        for a, d, e in bad[:16]:
            print(f'  {a:#09x} read {d:#010x} expected {e:#010x} (bits {d ^ e:#010x})')
    if count > 4096:
        print('\ndata log (kind, address, data):')
        for i in range(min(args.data, 1024)):
            a, d = words[4096 + 2 * i], words[4097 + 2 * i]
            if a == 0 and d == 0: break
            print(f"  {'W' if a >> 31 else 'R'} {a & 0x1ffffff:#09x} {d:#010x}")


if __name__ == '__main__': main()
