#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""gpu_rect: the last-row test computed beside the end-of-line test (clk2x timing).

Candidate D2 seed 6 missed clk2x setup on gpu_rect xCnt[*] -> state.* by
0.897 ns (Slow 0C) and 0.481 ns (Slow 85C). In the GPU block fit
(cl-gpu-mulla-block-20261111/base, worst_setup_paths.rpt path 1) the same
cone is 11 logic levels and 13.803 ns without chip congestion: xPos + 1,
the compare against drawingAreaRight, then the y adder (Add9) and its
compare (LessThan9), then the state multiplexers. The fitter put the
end-of-line term in front of the y adder, so the two add-and-compare stages
are in series although the source's y test does not depend on x.

This step moves the y test of PROCPIXELS out of the clocked process:

   yEndCntSum = yCnt + yAdd, yEndPosSum = yPos + yAdd        (kept nets)
   yEndNow    = yEndCntSum >= rec_sizey or yEndPosSum > drawingAreaBottom

with yAdd = 2 when interlacedDrawing = '1', else 1. The process reads
yEndNow where it had the expression. It is the same expression of the same
signals (yCnt, yPos, rec_sizey, drawingAreaBottom, interlacedDrawing) with
the same types (9-bit unsigned sum with wrap, 12-bit signed sum), evaluated
in the same clock, so every register loads the same value on every edge. No
register, no cycle and no input is added. The two sums carry `keep` so the
end-of-line term cannot be folded back into their adders.

Check: scripts/run_gpu_rect_yend.py (entity lockstep of the reviewed
gpu_rect against this step's output, every output port on every clk2x cycle,
planted faults --fault NAME).

Usage: prepare_gpu_rect_yend.py SOURCE --output OUTPUT [--seed N] [--baseline]
  SOURCE is a frozen project (apf_top.qsf, fit-inputs.json) whose gpu_rect.vhd
  is the reviewed input; it is never modified. OUTPUT (fresh directory) gets a
  relocated snapshot with the patched gpu_rect.vhd and a new fit-inputs.json.
  --baseline writes the same snapshot without the patch. SOURCE --text-only OUT
  transforms one gpu_rect.vhd file.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TAG = 'gpu-rect-yend-parallel'
# The reviewed input: build/ss/d2/gpu-no-watchdog/gpu_rect.vhd of the D2 project (2026-11-11);
# the C and M projects and the GPU block fit compile the same file.
SOURCE_SHA = '66f7b54a983c69c5878137668a0bc8509302eb8fca70a61da24b10c850037ad6'

DECL_OLD = '   signal firstPixel          : std_logic;\n'
DECL_NEW = (DECL_OLD +
            f'   -- {TAG}: the last-row test of PROCPIXELS, outside the clocked process\n'
            '   signal yEndCntSum          : unsigned(8 downto 0);\n'
            '   signal yEndPosSum          : signed(11 downto 0);\n'
            '   signal yEndNow             : std_logic;\n'
            '   attribute keep             : boolean;\n'
            '   attribute keep of yEndCntSum : signal is true;\n'
            '   attribute keep of yEndPosSum : signal is true;\n')

ANCHOR = "   vramLineAddr <= unsigned(xPos(9 downto 0)) when (state = PROCPIXELS) else (others => '0');\n"
STEP = "         if (interlacedDrawing = '1') then\n            yAddNow := 2;\n         end if;\n"
SUMS = '         yEndCntSum <= yCnt + yAddNow;\n         yEndPosSum <= yPos + yAddNow;\n'
TEST = "   yEndNow <= '1' when (yEndCntSum >= rec_sizey or yEndPosSum > to_integer(drawingAreaBottom)) else '0';\n"
BLOCK = ('   \n'
         f'   -- {TAG}: yCnt + yAdd and yPos + yAdd as the process forms them (same types, same wrap)\n'
         '   process (yCnt, yPos, interlacedDrawing)\n'
         '      variable yAddNow : integer range 1 to 2;\n'
         '   begin\n'
         '      yAddNow := 1;\n'
         + STEP.replace('         ', '      ', 1).replace('\n            ', '\n         ').replace('\n         end', '\n      end')
         + SUMS.replace('         ', '      ') +
         '   end process;\n'
         + TEST)

USE_OLD = '                        if (yCnt + yAdd >= rec_sizey or ypos + yAdd > to_integer(drawingAreaBottom)) then\n'
USE_NEW = f"                        if (yEndNow = '1') then -- {TAG}\n"

# Planted faults for the lockstep bench: (old, new) on the transformed text.
FAULTS = {
    # the row step of an interlaced rectangle is taken as 1
    'interlace_ignored': [("      if (interlacedDrawing = '1') then\n         yAddNow := 2;\n",
                           "      if (interlacedDrawing = '1') then\n         yAddNow := 1;\n")],
    # the drawing-area bottom no longer ends the rectangle
    'bottom_dropped': [(TEST, "   yEndNow <= '1' when (yEndCntSum >= rec_sizey) else '0';\n")],
    # the height test is one row late
    'size_off_by_one': [(TEST, TEST.replace('yEndCntSum >= rec_sizey', 'yEndCntSum > rec_sizey'))],
    # the row counter of the test is the current row, not the next one
    'count_not_advanced': [('      yEndCntSum <= yCnt + yAddNow;\n', '      yEndCntSum <= yCnt;\n')],
    # the bottom test is unsigned 9 bits (a negative y looks far below the area)
    'position_unsigned': [(TEST, TEST.replace('yEndPosSum > to_integer(drawingAreaBottom)',
                                              'unsigned(yEndPosSum(8 downto 0)) > drawingAreaBottom'))],
}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG}: expected exactly one match of: {old.strip()[:70]}')
    return text.replace(old, new)


def transform(text):
    """The reviewed gpu_rect.vhd with the parallel last-row test. Refuses any other input."""
    if TAG in text:
        raise ValueError(f'{TAG}: already applied')
    if sha(text) != SOURCE_SHA:
        raise ValueError(f'{TAG}: gpu_rect.vhd is not the reviewed input (sha256 {sha(text)[:16]}, want {SOURCE_SHA[:16]})')
    # The inventory the argument rests on: one row step, one last-row test, and the
    # row step is 1 unless interlacedDrawing = '1'.
    if (text.count("                        yAdd := 1;\n                        if (interlacedDrawing = '1') then\n"
                   '                           yAdd := 2;\n                        end if;\n') != 1
            or len(re.findall(r'\byAdd\s*:=', text)) != 2 or len(re.findall(r'\byAdd\b', text)) != 8):
        raise ValueError(f'{TAG}: row step changed')
    text = _once(text, DECL_OLD, DECL_NEW)
    text = _once(text, ANCHOR, ANCHOR + BLOCK)
    text = _once(text, USE_OLD, USE_NEW)
    for name, count in (('yEndNow', 3), ('yEndCntSum', 4), ('yEndPosSum', 4), ('yAddNow', 5)):
        if len(re.findall(rf'\b{name}\b', text)) != count:
            raise ValueError(f'{TAG}: unexpected use count of {name}')
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
    rows = [r for r in manifest['quartus_sources'] if Path(r['path']).name == 'gpu_rect.vhd']
    if len(rows) != 1 or rows[0]['sha256'] != SOURCE_SHA:
        raise SystemExit(f'{TAG}: {source} does not compile the reviewed gpu_rect.vhd')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    rect = next(Path(r['path']) for r in before if Path(r['path']).name == 'gpu_rect.vhd')
    if baseline:
        if sha(rect.read_text()) != SOURCE_SHA:
            raise SystemExit(f'{TAG}: baseline snapshot differs from the reviewed input')
        return rect, seed
    new = transform(rect.read_text())
    try:
        transform(new)
    except ValueError as error:
        if 'already applied' not in str(error):
            raise
    else:
        raise SystemExit(f'{TAG}: idempotence guard failed')
    rect.write_text(new)
    after = inputs(output)
    a, b = ({r['path']: r for r in rs} for rs in (after, before))
    if a.keys() != b.keys() or {p for p in b if a[p] != b[p]} != {str(rect)}:
        raise SystemExit(f'{TAG}: expected only gpu_rect.vhd to change')
    hold_guard(output, after)
    manifest['quartus_sources'] = after
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    if digest(source / rows[0]['path']) != SOURCE_SHA:
        raise SystemExit(f'{TAG}: the source project changed')
    return rect, seed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--baseline', action='store_true',
                        help='snapshot only, gpu_rect.vhd unchanged (same-layout baseline for the block fit)')
    parser.add_argument('--text-only', type=Path, metavar='OUT',
                        help='SOURCE is one gpu_rect.vhd; write the transformed text to OUT')
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
            rect, seed = prepare(args.source, args.output, args.seed, args.baseline)
            print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
            if args.baseline:
                print(f'{TAG}: baseline, unchanged {rect} (sha256 {sha(rect.read_text())})')
                raise SystemExit(0)
            print(f'{TAG}: patched {rect} (sha256 {sha(rect.read_text())})')
            print(f'{TAG}: no new register; the last-row test reads two kept sums beside the '
                  'end-of-line test; fit-inputs.json rewritten')
    except ValueError as error:
        raise SystemExit(str(error))
