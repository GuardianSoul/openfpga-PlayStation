# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""lab-room step for gpu_poly.vhd, stacked on lab_room_poly (parts "poly,polyflat"): the two
zero flags go, boundCoordUs / boundCoordLs are the lane quotients themselves.

Why the zeroing is not needed: a flag is set exactly when its half has zero height
(vt(1).y = vt(0).y for the upper half, vt(2).y = vt(1).y for the lower one; PREPAREHALF
loads yCoord / yBound from the same vertex pair). The zeroed step only feeds xStartNext /
xEndNext, which only PREPAREDECMODE and the PREPARELINE skip path load. In a zero-height
half PREPARELINE stops at once (yCoord >= yBound, or yCoord - 1 < yBound in decrementing
mode): no pixel is drawn, and what follows is PREPAREHALF, which reloads xStart / xEnd from
halfStart / halfCoord (independent of xStart), or the end of the primitive. Nothing else
reads boundCoordUs / Ls (rightFacing compares div2 / div1 quotients and vertex x). The
planted faults us-zero-lost / ls-zero-lost of lab_room_poly (each removes one flag) were
not detected by the whole-GPU lockstep, as this predicts.
"""

TAG = 'lab-room-polyflat'
POLY_TAG = 'lab-room-poly:'

EDITS = [
    ('''   signal boundUsZero         : std_logic := '0';
   signal boundLsZero         : std_logic := '0';
''', ''),
    ("   boundCoordUs <= (others => '0') when boundUsZero = '1' else div2.quotient;\n"
     "   boundCoordLs <= (others => '0') when boundLsZero = '1' else div3.quotient;\n",
     "   boundCoordUs <= div2.quotient;   -- lab-room-polyflat: no zero flags\n"
     "   boundCoordLs <= div3.quotient;\n"),
    ("                     boundUsZero <= '1';\n", ''),
    ("                     boundUsZero <= '0';\n", ''),
    ("                     boundLsZero <= '1';\n", ''),
    ("                     boundLsZero <= '0';\n", ''),
]

# Planted faults (bench only), applied to the transformed text
FAULTS = {
    # the lower edge steps by the upper edge's quotient
    'ls-from-us': ("   boundCoordLs <= div3.quotient;\n", "   boundCoordLs <= div2.quotient;\n"),
    # the upper edge steps by the base edge's quotient
    'us-from-base': ("   boundCoordUs <= div2.quotient;", "   boundCoordUs <= div1.quotient;"),
}


def once(text, old, new):
    n = text.count(old)
    if n != 1:
        raise ValueError(f'{TAG}: expected one match, found {n}: {old.strip()[:100]!r}')
    return text.replace(old, new)


def transform(text):
    if TAG in text:
        raise ValueError(f'{TAG}: already applied')
    if POLY_TAG not in text:
        raise ValueError(f'{TAG}: apply lab_room_poly first (parts poly,polyflat)')
    for old, new in EDITS:
        text = once(text, old, new)
    if 'boundUsZero' in text or 'boundLsZero' in text:
        raise ValueError(f'{TAG}: a zero flag is left')
    return text


def fault_text(text, name):
    return once(text, *FAULTS[name])
