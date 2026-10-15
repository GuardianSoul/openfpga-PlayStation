#!/usr/bin/env python3
"""Decode PSRAM stress probe result files (Saves/psxsweep/common/result-*.bin).

Each file holds the probe's words as saved by the Pocket when the core quits
(big-endian, rotated by one word):
magic "PSXR", done, operations, failures, {operation, halfword address} of
the first failure, its expected and actual words, and a second read of it.
"""
import argparse
from pathlib import Path
import struct

OPS = {0: 'masked write', 1: 'write+read', 2: 'line read', 3: 'word read', 4: 'halfword write',
       5: '2-word read / chained read', 6: 're-read', 15: 'error response'}


def describe(path):
    data = path.read_bytes()
    # The Pocket saves big-endian words, and the bridge read lags one word:
    # file word k is probe word k+1 (the magic, word 0, lands last).
    if len(data) != 64: return f'{path.name}: not a probe result ({len(data)} bytes)'
    file_words = struct.unpack('>16I', data)
    words = [file_words[(i - 1) % 16] for i in range(16)]
    if words[0] != 0x52585350: return f'{path.name}: not a probe result (no PSXR magic)'
    done, ops, fails, where, expected, actual, reread = words[1:8]
    state = 'PASS' if done and fails == 0 else 'FAIL' if fails else 'NOT DONE'
    line = f'{path.name}: {state} ops={ops:,} fails={fails:,}'
    if fails:
        op, addr = where >> 25 & 0xf, where & 0x1ffffff
        stored = 'stored wrong (write)' if reread == actual else 'read glitch' if reread == expected else 'unstable'
        line += (f'\n  first: {OPS.get(op, op)} halfword 0x{addr:07x} (die {addr >> 23 & 1}, word 0x{(addr >> 1) & 0x3fffff:x})'
                 f'\n  expected 0x{expected:08x} actual 0x{actual:08x} (diff 0x{expected ^ actual:08x})'
                 f' re-read 0x{reread:08x}: {stored}')
    return line


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='+', type=Path, help='result files or the folder holding them')
    args = parser.parse_args()
    for path in args.paths:
        for f in sorted(path.glob('result-*.bin')) if path.is_dir() else [path]:
            print(describe(f))


if __name__ == '__main__':
    main()
