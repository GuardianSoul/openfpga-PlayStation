#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""gpu_poly: gradient multiplier operands registered one clock early (clk2x timing).

Two card candidates missed clk2x setup on mulStep -> muldivResult1clk/2clk
(-0.165 ns Slow 0C, -0.086 ns Slow 85C, cl-ssM-signoff-20261111-s6): mulStep
and state select the channel of each vertex (dmV*), y or x (dmP*), then two
subtractions (dmD*/dmE*), the operand swap (dmEven) and the 9x13 mul9s DSP
product all sit between mulStep and the result registers.

This step keeps every expression but evaluates the selects for the NEXT
enabled clock and registers the four multiplier operands (44 register bits,
enable ce). The multiplier then starts from registers; mulStep/state/vt end
at the operand registers. No cycle is added and no result changes:

  * muldivResult1clk/2clk load only in CALCCOLOR2 (mulStep 0..5) and
    CALCTEXTURE2 (mulStep 0..3), under ce = '1' and reset = '0'.
  * state and mulStep change only at an edge with ce = '1' (the READWAIT exit
    and reset, which act with ce = '0', neither enter nor leave those states
    towards a capture). So the last enabled edge before a capture in
    (S2, k) was taken in (S1, any) for k = 0 -- S1 is CALCCOLOR1 /
    CALCTEXTURE1, the only predecessor, which writes mulStep <= 0 -- or in
    (S2, k - 1) for k > 0.
  * laStep is 0 in S1 and mulStep + 1 otherwise, and the texture select is
    (CALCTEXTURE1 or CALCTEXTURE2) and laStep < 4: exactly the (state,
    mulStep) the capture clock will see.
  * vt is written only in ISSUE_READ1-3 / SORT_READ1-3, never in or between
    those states, so the operand's sources do not move with the look-ahead.
  * The registers hold while ce = '0', as state and mulStep do.

Check: scripts/run_gpu_poly_mul_lookahead.py (entity lockstep of the reviewed
gpu_poly against this step's output, every output and both result registers
on every clk2x cycle, planted faults --fault stale_operand / enable_ignored /
first_ignored).

Usage: prepare_gpu_poly_mul_lookahead.py SOURCE --output OUTPUT [--seed N] [--baseline]
  SOURCE is a frozen project (apf_top.qsf, fit-inputs.json) whose gpu_poly.vhd
  is the reviewed input; it is never modified. OUTPUT (fresh directory) gets a
  relocated snapshot with the patched gpu_poly.vhd and a new fit-inputs.json.
  --baseline writes the same snapshot without the patch. SOURCE --text-only OUT
  transforms one gpu_poly.vhd file.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TAG = 'gpu-poly-mul-lookahead'
# The reviewed input: build/ss/cm/gpu-recip/gpu_poly.vhd of the M project (2026-11-11).
SOURCE_SHA = '94116afb6dc05fa53f2f196b925a2aa659a0eabeaea4048dda48b662253857b9'
NEW_BITS = 9 + 13 + 9 + 13

DECLS = [
    ('   signal muldiv1In1          : signed(8 downto 0);\n',
     f"   signal muldiv1In1          : signed(8 downto 0) := (others => '0'); -- {TAG}: register\n"
     f'   -- {TAG}: selects and operands of the next enabled clock\n'
     "   signal laFirst             : std_logic;\n"
     '   signal laStep              : integer range 0 to 7;\n'
     '   signal laIn11, laIn21      : signed(8 downto 0);\n'
     '   signal laIn12, laIn22      : signed(12 downto 0);\n'),
    ('   signal muldiv1In2          : signed(12 downto 0);\n',
     f"   signal muldiv1In2          : signed(12 downto 0) := (others => '0'); -- {TAG}: register\n"),
    ('   signal muldiv2In1          : signed(8 downto 0);\n',
     f"   signal muldiv2In1          : signed(8 downto 0) := (others => '0'); -- {TAG}: register\n"),
    ('   signal muldiv2In2          : signed(12 downto 0);\n',
     f"   signal muldiv2In2          : signed(12 downto 0) := (others => '0'); -- {TAG}: register\n"),
]

SELECT_OLD = ("   dmTex  <= '1' when (state = CALCTEXTURE2 and mulStep < 4) else '0';\n"
              "   dmEven <= '1' when (mulStep = 0 or mulStep = 2 or mulStep = 4) else '0';\n")
SELECT_NEW = (f'   -- {TAG}: dm* below are evaluated for the next enabled clock (laStep, not mulStep)\n'
              "   laFirst <= '1' when (state = CALCCOLOR1 or state = CALCTEXTURE1) else '0';\n"
              "   laStep  <= 0 when laFirst = '1' else (mulStep + 1) mod 8;\n"
              "   dmTex  <= '1' when ((state = CALCTEXTURE1 or state = CALCTEXTURE2) and laStep < 4) else '0';\n"
              "   dmEven <= '1' when (laStep = 0 or laStep = 2 or laStep = 4) else '0';\n")

OPERANDS = [("   muldiv1In1 <= dmD10 when dmEven = '1' else dmD21;\n", 'laIn11'),
            ("   muldiv1In2 <= dmE21 when dmEven = '1' else dmE10;\n", 'laIn12'),
            ("   muldiv2In1 <= dmD21 when dmEven = '1' else dmD10;\n", 'laIn21'),
            ("   muldiv2In2 <= dmE10 when dmEven = '1' else dmE21;\n", 'laIn22')]

ENABLE = f"         if (ce = '1') then -- {TAG}: hold with state and mulStep\n"
REGISTERS = (f'   process (clk2x) -- {TAG}: operand registers in front of the mul9s pair\n'
             '   begin\n'
             '      if rising_edge(clk2x) then\n'
             + ENABLE +
             '            muldiv1In1 <= laIn11;\n'
             '            muldiv1In2 <= laIn12;\n'
             '            muldiv2In1 <= laIn21;\n'
             '            muldiv2In2 <= laIn22;\n'
             '         end if;\n'
             '      end if;\n'
             '   end process;\n')
REGISTERS_AFTER = OPERANDS[-1][0].replace('muldiv2In2', 'laIn22')

# Planted faults for the lockstep bench (old, new) on the transformed text.
FAULTS = {
    # vertex 1's channel is selected by the current step: one operand source one step stale
    'stale_operand': [(f"   dmV1 <= vt(1).u when (dmTex = '1' and laStep < 2) else\n"
                       "            vt(1).v when dmTex = '1' else\n"
                       '            vt(1).r when laStep < 2 else\n'
                       '            vt(1).g when laStep < 4 else\n',
                       "   dmV1 <= vt(1).u when (dmTex = '1' and mulStep < 2) else\n"
                       "            vt(1).v when dmTex = '1' else\n"
                       '            vt(1).r when mulStep < 2 else\n'
                       '            vt(1).g when mulStep < 4 else\n')],
    # the operand registers load on every clock, also while ce = '0'
    'enable_ignored': [(ENABLE, '         if (true) then\n')],
    # the first step's operands are not prepared in CALCCOLOR1 / CALCTEXTURE1
    'first_ignored': [("   laStep  <= 0 when laFirst = '1' else (mulStep + 1) mod 8;\n",
                       '   laStep  <= (mulStep + 1) mod 8;\n')],
}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG}: expected exactly one match of: {old.strip()[:70]}')
    return text.replace(old, new)


def transform(text):
    """The reviewed gpu_poly.vhd with the operand look-ahead. Refuses any other input."""
    if TAG in text:
        raise ValueError(f'{TAG}: already applied')
    if sha(text) != SOURCE_SHA:
        raise ValueError(f'{TAG}: gpu_poly.vhd is not the reviewed input (sha256 {sha(text)[:16]}, want {SOURCE_SHA[:16]})')
    # The capture and source-write inventory the argument rests on.
    if (len(re.findall(r'muldivResult1clk <= muldivResult1; muldivResult2clk <= muldivResult2;', text)) != 10
            or len(re.findall(r'muldivResult[12]clk\s*<=', text)) != 20):
        raise ValueError(f'{TAG}: product captures changed')
    if len(re.findall(r'\bvt\(\d\)\s*<=', text)) != 6 or len(re.findall(r'\bvt\s*<=', text)) != 0:
        raise ValueError(f'{TAG}: vertex register writes changed')
    if len(re.findall(r'state\s*<=\s*CALCCOLOR2\b', text)) != 1 or len(re.findall(r'state\s*<=\s*CALCTEXTURE2\b', text)) != 1:
        raise ValueError(f'{TAG}: more than one way into a capture state')
    for old, new in DECLS:
        text = _once(text, old, new)
    text = _once(text, SELECT_OLD, SELECT_NEW)
    block_start = text.index(SELECT_NEW) + len(SELECT_NEW)
    block_end = text.index(OPERANDS[-1][0]) + len(OPERANDS[-1][0])
    block = text[block_start:block_end]
    if block.count('mulStep') != 9 or 'state' in block:
        raise ValueError(f'{TAG}: operand select block changed')
    text = text[:block_start] + block.replace('mulStep', 'laStep') + text[block_end:]
    for old, name in OPERANDS:
        text = _once(text, old, old.replace(old.split(' <=')[0].strip(), name, 1))
    text = _once(text, REGISTERS_AFTER, REGISTERS_AFTER + REGISTERS)
    # Nothing else may read the next-clock selects.
    for name, count in (('dmTex', 8), ('dmEven', 9), ('laStep', 16), ('laFirst', 3)):
        if len(re.findall(rf'\b{name}\b', text)) != count:
            raise ValueError(f'{TAG}: unexpected use count of {name}: {len(re.findall(rf"{chr(92)}b{name}{chr(92)}b", text))}')
    return text


def fault_text(text, fault):
    for old, new in FAULTS[fault]:
        text = _once(text, old, new)
    return text


def prepare(source, output, seed=None, baseline=False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import digest, hold_guard, load_source, snapshot
    source, output = Path(source).resolve(), Path(output).resolve()
    manifest = load_source(source, source / 'fit-inputs.json')
    rows = [r for r in manifest['quartus_sources'] if Path(r['path']).name == 'gpu_poly.vhd']
    if len(rows) != 1 or rows[0]['sha256'] != SOURCE_SHA:
        raise SystemExit(f'{TAG}: {source} does not compile the reviewed gpu_poly.vhd')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    poly = next(Path(r['path']) for r in before if Path(r['path']).name == 'gpu_poly.vhd')
    if baseline:
        if sha(poly.read_text()) != SOURCE_SHA:
            raise SystemExit(f'{TAG}: baseline snapshot differs from the reviewed input')
        return poly, seed
    new = transform(poly.read_text())
    try:
        transform(new)
    except ValueError as error:
        if 'already applied' not in str(error):
            raise
    else:
        raise SystemExit(f'{TAG}: idempotence guard failed')
    poly.write_text(new)
    after = inputs(output)
    a, b = ({r['path']: r for r in rs} for rs in (after, before))
    if a.keys() != b.keys() or {p for p in b if a[p] != b[p]} != {str(poly)}:
        raise SystemExit(f'{TAG}: expected only gpu_poly.vhd to change')
    hold_guard(output, after)
    manifest['quartus_sources'] = after
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    if digest(source / rows[0]['path']) != SOURCE_SHA:
        raise SystemExit(f'{TAG}: the source project changed')
    return poly, seed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--baseline', action='store_true',
                        help='snapshot only, gpu_poly.vhd unchanged (same-layout baseline for the block fit)')
    parser.add_argument('--text-only', type=Path, metavar='OUT',
                        help='SOURCE is one gpu_poly.vhd; write the transformed text to OUT')
    args = parser.parse_args()
    try:
        if args.text_only:
            if args.text_only.resolve() == args.source.resolve():
                raise SystemExit(f'{TAG}: refusing to overwrite the input')
            args.text_only.write_text(transform(args.source.read_text()))
            print(f'{TAG}: wrote {args.text_only} (sha256 {sha(args.text_only.read_text())})')
        else:
            if not args.output:
                parser.error('--output required')
            poly, seed = prepare(args.source, args.output, args.seed, args.baseline)
            print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
            if args.baseline:
                print(f'{TAG}: baseline, unchanged {poly} (sha256 {sha(poly.read_text())})')
                raise SystemExit(0)
            print(f'{TAG}: patched {poly} (sha256 {sha(poly.read_text())})')
            print(f'{TAG}: {NEW_BITS} new register bits (muldiv1In1 9, muldiv1In2 13, muldiv2In1 9, muldiv2In2 13), '
                  'enable ce; selects evaluated for the next enabled clock; fit-inputs.json rewritten')
    except ValueError as error:
        raise SystemExit(str(error))
