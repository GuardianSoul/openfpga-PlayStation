# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""lab-room step for gpu_line.vhd: the line steps read from the held divider quotients.

Before: PROCCALC3 copies div1/div2.quotient(43 downto 0) into stepDx / stepDy (2 x 44 FF) on
div1.done. After: stepDx / stepDy are those quotient bits. The packed divider holds its
quotient from done until the next start of the lane (lab_room_poly.py has the argument);
lanes 0-2 are started only by gpu_line's PROCCALC2 and gpu_poly's CALCBOUNDARY1, and the
GPU runs one primitive at a time, so the quotient equals the copy for as long as PROCPIXELS
uses it.

Check: scripts/run_lab_room_gpu.py (whole-GPU lockstep, lines and polylines in the traffic).
"""

TAG = 'lab-room-line'

EDITS = [
    ('''   signal stepDx              : signed(43 downto 0) := (others => '0');
   signal stepDy              : signed(43 downto 0) := (others => '0');
''', '''   signal stepDx              : signed(43 downto 0);   -- lab-room-line: the held lane quotients
   signal stepDy              : signed(43 downto 0);
'''),
    ('''                     stepDx   <= div1.quotient(43 downto 0);
                     stepDy   <= div2.quotient(43 downto 0);
''', ''),
    ('\nbegin \n', '''
begin

   stepDx <= div1.quotient(43 downto 0);   -- lab-room-line
   stepDy <= div2.quotient(43 downto 0);
'''),
]

FAULTS = {
    # x and y steps swapped
    'swap': ('   stepDx <= div1.quotient(43 downto 0);   -- lab-room-line\n   stepDy <= div2.quotient(43 downto 0);\n',
             '   stepDx <= div2.quotient(43 downto 0);   -- lab-room-line\n   stepDy <= div1.quotient(43 downto 0);\n'),
    # the y step off by half a pixel (fraction bit 31 flipped)
    'dy-half': ('   stepDy <= div2.quotient(43 downto 0);\n',
                '   stepDy <= div2.quotient(43 downto 0) xor x"00080000000";\n'),
}


def once(text, old, new):
    n = text.count(old)
    if n != 1:
        raise ValueError(f'{TAG}: expected one match, found {n}: {old.strip()[:100]!r}')
    return text.replace(old, new)


def transform(text):
    if TAG in text:
        raise ValueError(f'{TAG}: already applied')
    for old, new in EDITS:
        text = once(text, old, new)
    return text


def fault_text(text, name):
    return once(text, *FAULTS[name])
