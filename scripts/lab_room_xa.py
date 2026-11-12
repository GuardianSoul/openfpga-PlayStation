# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""lab-room step for cd_xa.vhd (cd-xa-history-clamp variant): ADPCM history kept in 16 bits.

Before: AdpcmLast0/1_L/R are 24-bit and adpcm_mul1 (the multiplier operand chosen from them)
is 24-bit. Every write of the history is clampHistory(sample) (-32768..32767), a copy of
another history register, or the reset to zero, so bits 23:16 always equal bit 15.
After: the four history registers and adpcm_mul1 are 16-bit; the product sign-extends the
operand (resize to 24 bits) so adpcm_mulres is the same 32-bit value. clampHistory's result
is resized to 16 bits, which keeps its value. Removed: 4 x 8 + 8 = 40 FF and 8 bits of the
operand multiplexer.

Check: scripts/run_lab_room_xa.py (reference and candidate cd_xa on the same sectors,
every 44.1 kHz frame compared).
"""

TAG = 'lab-room-xa'

EDITS = [
    ('''   signal AdpcmLast0_L   : signed(23 downto 0);
   signal AdpcmLast0_R   : signed(23 downto 0);
   signal AdpcmLast1_L   : signed(23 downto 0);
   signal AdpcmLast1_R   : signed(23 downto 0);
''', '''   -- lab-room-xa: the history is always clamped to 16 bits
   signal AdpcmLast0_L   : signed(15 downto 0);
   signal AdpcmLast0_R   : signed(15 downto 0);
   signal AdpcmLast1_L   : signed(15 downto 0);
   signal AdpcmLast1_R   : signed(15 downto 0);
'''),
    ('   signal adpcm_mul1     : signed(23 downto 0);\n', '   signal adpcm_mul1     : signed(15 downto 0);   -- lab-room-xa\n'),
    ('         adpcm_mulres <= adpcm_mul1 * adpcm_mul2;\n', '         adpcm_mulres <= resize(adpcm_mul1, 24) * adpcm_mul2;   -- lab-room-xa\n'),
    ('                     AdpcmLast0_R <= clampHistory(sample);\n', '                     AdpcmLast0_R <= resize(clampHistory(sample), 16);\n'),
    ('                     AdpcmLast0_L <= clampHistory(sample);\n', '                     AdpcmLast0_L <= resize(clampHistory(sample), 16);\n'),
]

FAULTS = {
    # history kept in 15 bits: -32768 / large values wrap
    'narrow-15': ('                     AdpcmLast0_R <= resize(clampHistory(sample), 16);\n',
                  '                     AdpcmLast0_R <= resize(resize(clampHistory(sample), 15), 16);\n'),
    # operand zero-extended instead of sign-extended
    'zero-extend': ('resize(adpcm_mul1, 24) * adpcm_mul2', "signed(x\"00\" & std_logic_vector(adpcm_mul1)) * adpcm_mul2"),
}


def once(text, old, new):
    n = text.count(old)
    if n != 1:
        raise ValueError(f'{TAG}: expected one match, found {n}: {old.strip()[:100]!r}')
    return text.replace(old, new)


def transform(text):
    if TAG in text:
        raise ValueError(f'{TAG}: already applied')
    if 'function clampHistory' not in text:
        raise ValueError(f'{TAG}: needs the cd-xa-history-clamp variant')
    for old, new in EDITS:
        text = once(text, old, new)
    return text


def fault_text(text, name):
    return once(text, *FAULTS[name])
