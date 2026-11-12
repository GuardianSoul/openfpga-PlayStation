#!/usr/bin/env python3
"""CPU DIV/DIVU: divide-by-zero and 0x80000000 / -1 come from the divider.

The upstream CPU special-cases DIV/DIVU by zero and signed 0x80000000 / -1:
it loads two extra 32-bit registers (DIV0remainder, DIV0quotient) with the
PS1 results and selects them into HI/LO (HILOCALC_DIV0) instead of the
divider's outputs. The selected divider (rtl/pocket/psx_divider_bank.vhd,
the signed restoring PSX_MiSTer algorithm) already produces exactly those
values in its low 32 bits by the time HI/LO are loaded (hiloWait = 2, 36
clk1x edges after DIVstart, the same edge that loads every other DIV):

  DIVU x / 0      restoring with a zero divisor sets every quotient bit,
                  remainder = x                     -> LO FFFFFFFF, HI x
  DIV  x / 0      the same magnitudes; the quotient is negated when x < 0
                  (sign(x) xor sign(0)): -(2^33-1) mod 2^32 = 1
                                                    -> LO FFFFFFFF or 1, HI x
  DIV 80000000/-1 |x| / 1 = 2^31 in the 33-bit datapath, signs equal
                                                    -> LO 80000000, HI 0

DIVstart already fires for these cases (it does not look at hilocalc), so
the divider runs anyway; only the select changes. HI/LO, the stall and the
hiloWait schedule are identical, so the CPU is cycle-identical. Checked by
scripts/run_cpu_div_shared.py (paired lockstep CPUs on the divider program,
all edge pairs) with planted faults.

Usage: prepare_cpu_div_shared.py PROJECT (after the cpu save-state step)
"""
from pathlib import Path
import re
import sys

EDITS = [
    ('''                     if (value2 = 0) then
                        hilocalc      <= HILOCALC_DIV0;
                        DIV0remainder <= value1;
                        if (signed(value1) >= 0) then
                           DIV0quotient <= (others => '1');
                        else
                           DIV0quotient <= x"00000001";
                        end if;
                     elsif (value1 = x"80000000" and value2 = x"FFFFFFFF") then
                        hilocalc      <= HILOCALC_DIV0;
                        DIV0quotient  <= x"80000000";
                        DIV0remainder <= (others => '0');
                     else
                        hilocalc    <= HILOCALC_DIV;
                        --DIVstart    <= '1';
                        --DIVdividend <= resize(signed(value1), 33);
                        --DIVdivisor  <= resize(signed(value2), 33);
                     end if;
''', '''                     -- prepare_cpu_div_shared: /0 and 80000000/-1 results come from the divider
                     hilocalc    <= HILOCALC_DIV;
'''),
    ('''                     if (value2 = 0) then
                        hilocalc      <= HILOCALC_DIV0;
                        DIV0remainder <= value1;
                        DIV0quotient  <= (others => '1');
                     else
                        hilocalc    <= HILOCALC_DIVU;
                        --DIVstart    <= '1';
                        --DIVdividend <= '0' & signed(value1);
                        --DIVdivisor  <= '0' & signed(value2);
                     end if;
''', '''                     -- prepare_cpu_div_shared: /0 results come from the divider
                     hilocalc    <= HILOCALC_DIVU;
'''),
    ('''                  when HILOCALC_DIV0  => hi <=           DIV0remainder;               lo <=            DIV0quotient;
''', '''                  when HILOCALC_DIV0  => null; -- prepare_cpu_div_shared: never selected
'''),
]


def div_shared_text(text):
    if 'prepare_cpu_div_shared' in text:
        raise ValueError('cpu-div-shared: already applied')
    for old, new in EDITS:
        if text.count(old) != 1:
            raise ValueError('cpu-div-shared: cpu.vhd changed: ' + old.strip().splitlines()[0][:70])
        text = text.replace(old, new)
    left = [l for l in text.splitlines() if re.search(r'DIV0(remainder|quotient)\s*<=|<=\s*DIV0', l)]
    if left:
        raise ValueError('cpu-div-shared: DIV0 register use left: ' + left[0].strip())
    return text


def prepare_project(project):
    project = Path(project).resolve(); qsf = project/'apf_top.qsf'; q = qsf.read_text()
    hits = re.findall(r'"([^"]*/cpu\.vhd)"', q)
    if len(hits) != 1:
        raise ValueError('cpu-div-shared: expected one cpu.vhd in the project')
    src = Path(hits[0]); text = div_shared_text(src.read_text())
    out = project/'cpu-div-shared'/'cpu.vhd'; out.parent.mkdir(exist_ok=True)
    out.write_text('-- Modified by scripts/prepare_cpu_div_shared.py: see its docstring.\n' + text)
    qsf.write_text(q.replace(f'"{src}"', f'"{out}"'))
    return out


if __name__ == '__main__':
    print(prepare_project(sys.argv[1]))
