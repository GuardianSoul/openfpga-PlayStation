#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""cd_top: the subchannel frame number to BCD from a 128-entry table.

The subchannel position (readSubchannelState CALCPOS/CALCSECTOR) writes the
7-bit frameLeft as two BCD digits, resize(frameLeft / 10, 4) and
resize(frameLeft mod 10, 4). Quartus builds both as lpm_divide (Div0 42 and
Mod0 48 ALUTs in the ss7a map). Here a constant table holds both digits for
every 7-bit value (tens resized to 4 bits as before: 12 for 120..127), so the
same 8 bits come from a 7-input lookup. Same clock, same register, every
input value the same (exhaustive: run_cd_bcd_lean.py; clock level:
run_cd_lockstep.py).
Usage: prepare_cd_bcd_lean.py PROJECT_OR_cd_top.vhd
"""
import sys
from pathlib import Path

ANCHOR = '   function getSectorsPerTrack(lba : integer) return integer is\n'


def table_entry(x):
    return ((x // 10) % 16) * 16 + x % 10


TABLE = ('   -- prepare_cd_bcd_lean.py: frame number 0..127 to (x / 10) & (x mod 10), both resized to 4 bits\n'
         '   type t_frame_bcd is array(0 to 127) of std_logic_vector(7 downto 0);\n'
         '   constant FRAME_BCD : t_frame_bcd := (\n' +
         ',\n'.join('      ' + ', '.join(f'x"{table_entry(x):02X}"' for x in range(row, row + 8)) for row in range(0, 128, 8)) +
         ');\n\n')

SITES = [(f'''                        nextSubdata({n})(7 downto 4) <= std_logic_vector(resize(frameLeft / 10, 4));
                        nextSubdata({n})(3 downto 0) <= std_logic_vector(resize(frameLeft mod 10, 4));
''', f'''                        nextSubdata({n})             <= FRAME_BCD(to_integer(frameLeft));   -- prepare_cd_bcd_lean.py
''') for n in (5, 9)]


def patch(text):
    if 'prepare_cd_bcd_lean' in text:
        raise SystemExit('cd_top.vhd: prepare_cd_bcd_lean already applied')
    for old, new in SITES + [(ANCHOR, TABLE + ANCHOR)]:
        if text.count(old) != 1:
            raise SystemExit(f'cd_top.vhd: BCD anchor not unique: {old.strip().splitlines()[0][:70]}')
        text = text.replace(old, new)
    if 'frameLeft / 10' in text or 'frameLeft mod 10' in text:
        raise SystemExit('cd_top.vhd: a frameLeft division is left')
    return text


def target(path):
    path = Path(path)
    if path.is_file():
        return path
    hits = {line.split('"')[1] for line in (path / 'apf_top.qsf').read_text().splitlines() if '/cd_top.vhd"' in line}
    if len(hits) != 1:
        raise SystemExit(f'{path}: expected one assigned cd_top.vhd, found {sorted(hits)}')
    return Path(hits.pop())


if __name__ == '__main__':
    p = target(sys.argv[1])
    p.write_text(patch(p.read_text()))
    print(p)
