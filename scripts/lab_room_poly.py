# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""lab-room step for gpu_poly.vhd: edge steps read from the held divider quotients.

Before: CALCBOUNDARY2 copies the three lane quotients into baseStep / boundCoordUs /
boundCoordLs (3 x 45 FF) and PREPAREHALF copies two of those into xStepStart / xStepEnd
(2 x 45 FF), chosen by rightFacing and the half.

Why the copies are not needed: the packed divider (divider-bank/divider.vhd) updates its
quotient register only from work_q / sign_n / sign_d, which change only while a division
runs. Lanes 0-2 are started only by gpu_poly's CALCBOUNDARY1 and by gpu_line, and the GPU
runs one primitive at a time, so from div1.done (the cycle CALCBOUNDARY2 leaves with
the final copy) until gpu_poly's next CALCBOUNDARY1 the quotients equal the copies.
All readers (the half-pipe product, xStartNext / xEndNext) use them only after that.

After: baseStep is div1.quotient; boundCoordUs / Ls are div2 / div3.quotient gated by two
flags latched where the old code chose zero; xStepStart / xStepEnd are a combinational
choice by rightFacing and secondhalf (secondhalf = 1 while the first half draws, 0 while
the second half draws, exactly the branch PREPAREHALF took). baseCoord keeps its 13 live
bits (vt(0).x); its low 32 bits are always 0xFFFFF800.

Check: scripts/run_lab_room_poly.py (entity lockstep against the snapshot's gpu_poly).
"""

import re

TAG = 'lab-room-poly'

EDITS = [
    # declarations
    ('''   signal baseCoord           : signed(44 downto 0) := (others => '0');
   signal baseStep            : signed(44 downto 0) := (others => '0');
   signal boundCoordUs        : signed(44 downto 0) := (others => '0');
   signal boundCoordLs        : signed(44 downto 0) := (others => '0');
''', '''   signal baseCoordX          : signed(12 downto 0) := (others => '0');   -- lab-room-poly: the live bits of baseCoord
   signal baseCoord           : signed(44 downto 0);
   signal baseStep            : signed(44 downto 0);
   signal boundCoordUs        : signed(44 downto 0);
   signal boundCoordLs        : signed(44 downto 0);
   signal boundUsZero         : std_logic := '0';
   signal boundLsZero         : std_logic := '0';
'''),
    ('''   signal xStepStart          : signed(44 downto 0) := (others => '0');
''', '''   signal xStepStart          : signed(44 downto 0);
'''),
    ('''   signal xStepEnd            : signed(44 downto 0) := (others => '0');
''', '''   signal xStepEnd            : signed(44 downto 0);
'''),
    # combinational views
    ('''   xStepSub   <= '1' when decMode = 1 else '0';
''', '''   -- lab-room-poly: the steps read from the held lane quotients (no copies).
   baseCoord    <= baseCoordX & x"FFFFF800";
   baseStep     <= div1.quotient;
   boundCoordUs <= (others => '0') when boundUsZero = '1' else div2.quotient;
   boundCoordLs <= (others => '0') when boundLsZero = '1' else div3.quotient;
   xStepStart   <= baseStep when rightFacing = 1 else boundCoordUs when secondhalf = '1' else boundCoordLs;
   xStepEnd     <= baseStep when rightFacing = 0 else boundCoordUs when secondhalf = '1' else boundCoordLs;

   xStepSub   <= '1' when decMode = 1 else '0';
'''),
    ('''                  baseCoord <= (to_signed(vt(0).x, 13) & x"00000000") + x"100000000" - 2048;
''', '''                  baseCoordX <= to_signed(vt(0).x, 13);
'''),
    # CALCBOUNDARY2: the copies become two zero flags
    ("                  baseStep     <= div1.quotient;\n", ""),
    ("                     boundCoordUs <= (others => '0');\n", "                     boundUsZero <= '1';\n"),
    ("                     boundCoordUs <= div2.quotient;\n", "                     boundUsZero <= '0';\n"),
    ("                     boundCoordLs <= (others => '0');\n", "                     boundLsZero <= '1';\n"),
    ("                     boundCoordLs <= div3.quotient;\n", "                     boundLsZero <= '0';\n"),
]
# PREPAREHALF: the eight step copies (two per branch) go; the combinational choice replaces them
STEP_COPY = re.compile(r'(?m)^ {24}xStep(?:Start|End) +<= (?:baseStep|boundCoordUs|boundCoordLs);\n')

# Planted faults (bench only): name -> (old, new) applied to the transformed text
FAULTS = {
    # the half choice inverted: the first half steps along the lower edge
    'half-swap': ("boundCoordUs when secondhalf = '1' else boundCoordLs;\n   xStepEnd",
                  "boundCoordLs when secondhalf = '1' else boundCoordUs;\n   xStepEnd"),
    # the flat upper edge no longer steps by zero
    'us-zero-lost': ("boundCoordUs <= (others => '0') when boundUsZero = '1' else div2.quotient;",
                     "boundCoordUs <= div2.quotient;"),
    # the lower-edge zero flag never set
    'ls-zero-lost': ("                     boundLsZero <= '1';\n", "                     boundLsZero <= '0';\n"),
    # baseCoord's constant fraction off by half a pixel
    'base-half': ('baseCoordX & x"FFFFF800"', 'baseCoordX & x"7FFFF800"'),
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
    text, n = STEP_COPY.subn('', text)
    if n != 8:
        raise ValueError(f'{TAG}: expected eight step copies in PREPAREHALF, found {n}')
    return text


def fault_text(text, name):
    return once(text, *FAULTS[name])
