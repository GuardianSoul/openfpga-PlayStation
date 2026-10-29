#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""gpu_line builds its two slope dividends without 45-bit adders.

PROCCALC2 forms div1/div2.dividend as (d << 32) - (points - 1) for a negative
d and (d << 32) + (points - 1) otherwise (d = 12-bit dx or dy, points 10 bits).
The low 32 bits of d << 32 are zero and points - 1 < 1024, so the sum is just:
- d >= 0: high 13 bits d, low 32 bits points - 1;
- d < 0:  high 13 bits d - 1 and low 32 bits -(points - 1) mod 2^32, unless
  points - 1 = 0 (points is unsigned: points = 0 gives 1023).
PROCCALC1 orders the endpoints by x, so dx is never negative in PROCCALC2 and
div1 keeps only the d >= 0 form (the step checks the swap is present). div2
(dy can be negative) gets the direct form: a 13-bit conditional decrement and
a 10-bit negate instead of two full-width add/subtract chains.
Bit-exact: every dividend equals the original for all d and points (checked
exhaustively over d in -2048..2047 and points in 0..1023 by the transform's
self-test, and in the GPU line oracle).
Usage: prepare_gpu_line_dividend.py PROJECT  (post-prepare step; patches the
gpu_line.vhd the project's apf_top.qsf compiles)
"""
import re
import sys
from pathlib import Path

TAG = 'prepare_gpu_line_dividend'


def old_lines(n, v):
    return (f'                  if ({v} < 0) then div{n}.dividend <= (resize({v}, 13) & x"00000000") - to_integer(points - 1);\n'
            f'                  else             div{n}.dividend <= (resize({v}, 13) & x"00000000") + to_integer(points - 1); end if;\n')


def new_lines(n, v):
    return (f'                  -- {TAG}: (d << 32) +/- (points - 1) without a 45-bit adder\n'
            f'                  if ({v} < 0 and points /= 1) then div{n}.dividend <= (resize({v}, 13) - 1) & signed(x"FFFFF" & "11" & std_logic_vector(1 - points));\n'
            f'                  elsif ({v} < 0) then div{n}.dividend <= resize({v}, 13) & x"00000000";\n'
            f'                  else div{n}.dividend <= resize({v}, 13) & signed(x"00000" & "00" & std_logic_vector(points - 1)); end if;\n')


SWAP = ("                  if (proc_pos1x >= proc_pos2x) then\n"
        "                     proc_pos1x  <= proc_pos2x; \n"
        "                     proc_pos1y  <= proc_pos2y; \n"
        "                     proc_pos2x  <= proc_pos1x; \n"
        "                     proc_pos2y  <= proc_pos1y; \n")


def transform(text):
    if TAG in text:
        return text
    # PROCCALC1 swaps the endpoints whenever pos1x >= pos2x, so PROCCALC2 always sees dx >= 0:
    # div1 keeps only the d >= 0 form.
    if text.count(SWAP) != 1:
        raise ValueError('PROCCALC1 endpoint swap: expected exactly one match')
    old = old_lines('1', 'dx')
    if text.count(old) != 1:
        raise ValueError('div1 dividend: expected exactly one match')
    text = text.replace(old, f'                  -- {TAG}: dx >= 0 here (PROCCALC1 orders the endpoints by x)\n'
                        '                  div1.dividend <= resize(dx, 13) & signed(x"00000" & "00" & std_logic_vector(points - 1));\n')
    old = old_lines('2', 'dy')
    if text.count(old) != 1:
        raise ValueError('div2 dividend: expected exactly one match')
    return text.replace(old, new_lines('2', 'dy'))


def reference(d, points):
    """The original: points - 1 on unsigned(9 downto 0) wraps (points = 0 gives 1023)."""
    pm1 = (points - 1) & 0x3FF
    value = (d << 32) - pm1 if d < 0 else (d << 32) + pm1
    return value & ((1 << 45) - 1)


def rewritten(d, points):
    """The rewritten bit construction."""
    pm1 = (points - 1) & 0x3FF
    if d < 0 and points != 1:
        return (((d - 1) & 0x1FFF) << 32) | (0xFFFFFC00 | ((1 - points) & 0x3FF))
    if d < 0:
        return (d & 0x1FFF) << 32
    return ((d & 0x1FFF) << 32) | pm1


def self_test():
    for d in range(-2048, 2048):
        for points in range(0, 1024):
            if reference(d, points) != rewritten(d, points):
                raise AssertionError(f'mismatch d={d} points={points}')


def apply(project):
    project = Path(project).resolve()
    qsf = (project / 'apf_top.qsf').read_text()
    lines = sorted({Path(p) for p in re.findall(r'VHDL_FILE "([^"]*/gpu_line\.vhd)"', qsf)})
    if len(lines) != 1:
        raise SystemExit(f'{TAG}: expected one compiled gpu_line.vhd, found {len(lines)}')
    path = lines[0].resolve()
    if project not in path.parents:
        raise SystemExit(f'{TAG}: {path} is outside the project; refusing to patch a shared source')
    try:
        path.write_text(transform(path.read_text()))
    except ValueError as error:
        raise SystemExit(f'{TAG}: {path}: {error}')
    return path


if __name__ == '__main__':
    if sys.argv[1:] == ['--self-test']:
        self_test(); print('self-test passed'); raise SystemExit(0)
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.split('Usage: ')[1])
    print('patched', apply(sys.argv[1]))
