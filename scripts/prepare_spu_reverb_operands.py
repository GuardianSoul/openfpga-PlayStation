#!/usr/bin/env python3
"""SPU reverb: the IIASM factor on the 18-bit multiplier operand.

The SPU shares one multiplier, soundmul1 (24 bits) * soundmul2 (16 bits),
between the reverb, CD volume and main volume steps. Two IIR steps put a
read value on soundmul2 and IIASM(vIIR) (0, -65536 or 32768 - vIIR, which
need 18 bits) on soundmul1. Here soundmul2 is 18 bits wide and the two
factors trade places: the read value joins soundmul1's existing read-value
sources and soundmul2 takes IIASM. The product, and so every result, is the
same integer; soundmul1 loses three sources and soundmul2 two.
Usage: prepare_spu_reverb_operands.py PROJECT (SPU helpers before save states)
"""
from pathlib import Path
import re
import sys


def _once(text, old, new, what):
    if text.count(old) != 1: raise ValueError(f'spu-reverb-operands: {what} anchor found {text.count(old)} times')
    return text.replace(old, new)


IIASM_OLD = '''                        soundmul2 <= reverbReadval{n};
                        if (signed(rv_engine_q) = -32768) then
                           if (reverbReadval{n} = -32768) then
                              soundmul1 <= (others => '0');
                           else
                              soundmul1 <= to_signed(-65536, 24);
                           end if;
                        else
                           soundmul1 <= to_signed(32768, 24) - resize(signed(rv_engine_q), 24);
                        end if;
'''
IIASM_NEW = '''                        soundmul1 <= resize(reverbReadval{n}, 24);   -- spu-reverb-operands: factors swapped
                        if (signed(rv_engine_q) = -32768) then
                           if (reverbReadval{n} = -32768) then
                              soundmul2 <= (others => '0');
                           else
                              soundmul2 <= to_signed(-65536, 18);
                           end if;
                        else
                           soundmul2 <= to_signed(32768, 18) - resize(signed(rv_engine_q), 18);
                        end if;
'''


def operands_text(text):
    if 'spu-reverb-operands' in text: raise ValueError('spu-reverb-operands already applied')
    text = _once(text, '   signal soundmul2              : signed(15 downto 0);\n',
                 '   signal soundmul2              : signed(17 downto 0);   -- spu-reverb-operands: IIASM fits\n', 'soundmul2')
    text = _once(text, '      variable soundmulresult      : signed(39 downto 0);\n',
                 '      variable soundmulresult      : signed(41 downto 0);\n', 'soundmulresult')
    # the other 16-bit factors, sign-extended
    text, n = re.subn(r'(soundmul2 <= )(signed\([^;]*\)|reverbReadval\d|apf\dneg);', r'\1resize(\2, 18);', text)
    for k in (3, 4):
        text = _once(text, IIASM_OLD.format(n=k).replace('soundmul2 <= reverbReadval', 'soundmul2 <= resize(reverbReadval').replace(f'reverbReadval{k};\n', f'reverbReadval{k}, 18);\n', 1),
                     IIASM_NEW.format(n=k), f'IIASM step (value {k})')
    if re.search(r'soundmul2 <= (?!resize\(|\(others|to_signed)', text): raise ValueError('spu-reverb-operands: a 16-bit soundmul2 source is left')
    return text


def prepare_project(project):
    project = Path(project).resolve(); qsf = project/'apf_top.qsf'; q = qsf.read_text()
    hits = re.findall(r'"([^"]*/spu\.vhd)"', q)
    if len(hits) != 1: raise ValueError('spu-reverb-operands: expected one spu.vhd in the project')
    out = project/'spu-reverb-operands'/'spu.vhd'; out.parent.mkdir(exist_ok=True)
    out.write_text(operands_text(Path(hits[0]).read_text()))
    qsf.write_text(q.replace(f'"{hits[0]}"', f'"{out}"'))
    return out


if __name__ == '__main__':
    if len(sys.argv) != 2: raise SystemExit(__doc__.split('Usage: ')[1])
    print(prepare_project(sys.argv[1]))
