#!/usr/bin/env python3
"""MULT and MULTU on one multiplier (patch for MiSTer's rtl/cpu.vhd, GPL-2.0).

MiSTer's cpu.vhd registers two 32x32 products every clock enable, a signed
one (mulResultS) and an unsigned one (mulResultU), and picks one at
hiloWait = 2 by hilocalc. This patch builds a single 33x33 signed product
instead: each operand gets a 33rd bit that is its sign bit for MULT and 0 for
MULTU, so the low 64 bits are the signed or the unsigned 64-bit product.

The signedness comes from hilocalc, which the launch sets together with
mul1/mul2, so it is fixed when the operation starts. The product register
is loaded at the same clock enables as before, so hi/lo get the same values
at the same clock (hiloWait = 2), and every MULT/MULTU wait is unchanged.

Usage as a module: shared_product_text(cpu_vhd_text) -> patched text.
"""
import re
import sys

MARK = 'cpu-shared-product'


def _sub_once(text, pattern, repl, what):
    new, n = re.subn(pattern, repl, text, flags=re.M)
    if n != 1: raise ValueError(f'{MARK}: {what} anchor found {n} times')
    return new


def shared_product_text(t):
    if 'mulProduct' in t: raise ValueError(f'{MARK}: already applied')
    # The two product registers become one 66-bit product of two 33-bit operands.
    t = _sub_once(t, r'^([ \t]*)signal mulResultS([ \t]*): signed\(63 downto 0\);\n[ \t]*signal mulResultU[ \t]*: unsigned\(63 downto 0\);\n',
                  lambda m: (f'{m.group(1)}signal mulProduct{m.group(2)}: signed(65 downto 0);   -- {MARK}: MULT and MULTU\n'
                             f'{m.group(1)}signal mulOperand1{m.group(2)[1:]}: signed(32 downto 0);   -- {MARK}\n'
                             f'{m.group(1)}signal mulOperand2{m.group(2)[1:]}: signed(32 downto 0);   -- {MARK}\n'),
                  'product signals')
    t = _sub_once(t, r'^([ \t]*)mulResultS <= signed\(mul1\) \* signed\(mul2\);\n[ \t]*mulResultU <= mul1 \* mul2;\n',
                  lambda m: f'{m.group(1)}mulProduct <= mulOperand1 * mulOperand2;   -- {MARK}\n',
                  'product assignments')
    t = _sub_once(t, r'^([ \t]*)when HILOCALC_MULT[ \t]*=> hi <=[ \t]*unsigned\(mulResultS\(63 downto 32\)\);[ \t]*lo <=[ \t]*unsigned\(mulResultS\(31 downto 0\)\);\n'
                     r'[ \t]*when HILOCALC_MULTU => hi <=[ \t]*mulResultU\(63 downto 32\);[ \t]*lo <=[ \t]*mulResultU\(31 downto 0\);\n',
                  lambda m: (f'{m.group(1)}when HILOCALC_MULT | HILOCALC_MULTU =>   -- {MARK}\n'
                             f'{m.group(1)}   hi <= unsigned(mulProduct(63 downto 32)); lo <= unsigned(mulProduct(31 downto 0));\n'),
                  'hi/lo select')
    # Operand sign bits: hilocalc and mul1/mul2 are loaded together at a
    # MULT/MULTU launch, so the extension is fixed for the whole operation.
    t = _sub_once(t, r'^end architecture;',
                  (f'   -- {MARK}: 33-bit operands, sign-extended for MULT, zero-extended for MULTU\n'
                   "   mulOperand1 <= signed(mul1(31) & mul1) when (hilocalc = HILOCALC_MULT) else signed('0' & mul1);\n"
                   "   mulOperand2 <= signed(mul2(31) & mul2) when (hilocalc = HILOCALC_MULT) else signed('0' & mul2);\n\n"
                   'end architecture;'),
                  'architecture end')
    if 'mulResultS' in t or 'mulResultU' in t: raise ValueError(f'{MARK}: another consumer of the old products')
    return t

if __name__ == '__main__':
    if len(sys.argv) != 3: raise SystemExit('usage: cpu_shared_product.py IN_CPU_VHD OUT_CPU_VHD')
    src = open(sys.argv[1]).read()
    open(sys.argv[2], 'w').write(shared_product_text(src))
