#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check prepare_cue_hold_cells.py on psx_cue_mount.sv.

psx_hold_cell is one identity LUT (lut_mask AAAA..., combout = dataa), so the
step is a combinational identity; cells are buffers. Three checks:
 1. text: taking the cell block out and putting the lanes back on db_dat gives
    the input file byte for byte, and the leaf is the reviewed identity LUT;
 2. run_cue_mount.py's cases (sim/pocket/tb_cue_mount.sv, Icarus) on the
    celled file with the cell modelled as a buffer: every case must pass;
 3. each planted fault of the step (FAULTS) must fail at least one of the
    FAULT_CASES.
Usage: run_cue_hold_cells.py [FILE] [--cases NAME ...] [--no-faults]
       FILE: the psx_cue_mount.sv before the step (default rtl/pocket/psx_cue_mount.sv)
"""
import argparse
import contextlib
import io
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_paths import ROOT, find_tool
import prepare_cue_hold_cells as step
import run_cue_mount as bench

BUFFER = 'module psx_hold_cell(input wire i, output wire o); assign o = i; endmodule\n'
FAULT_CASES = ('redump', 'merged')


def leaf_is_identity():
    leaf = ROOT / 'rtl/pocket/psx_hold_cell.sv'
    text = leaf.read_text()
    return (step.sha(text) == step.LEAF and "64'hAAAAAAAAAAAAAAAA" in text
            and re.search(r'\.dataa\(i\),\s*\.combout\(o\)', text) is not None)


def build(work, mount, iverilog):
    serv = ROOT / 'references/serv/rtl'
    fw = bench.bench_firmware(work)
    (work / 'hold.sv').write_text(BUFFER)
    for vvp_name, lc, ch in (('tb.vvp', 0, 0), ('tb_lc.vvp', 1, 0), ('tb_ch.vvp', 1, 1)):
        subprocess.run([iverilog, '-g2012', '-o', str(work / vvp_name), f'-Ptb_cue_mount.FW="{fw}"',
                        f'-Ptb_cue_mount.LC={lc}', f'-Ptb_cue_mount.CH={ch}', *[str(p) for p in bench.SERV_FILES(serv)],
                        str(mount), str(work / 'hold.sv'), str(ROOT / 'sim/pocket/tb_cue_mount.sv')], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('file', type=Path, nargs='?', default=ROOT / 'rtl/pocket/psx_cue_mount.sv')
    parser.add_argument('--cases', nargs='*', default=[])
    parser.add_argument('--no-faults', action='store_true')
    args = parser.parse_args()
    iverilog, vvp = find_tool('iverilog'), find_tool('vvp')
    if not iverilog or not vvp:
        raise SystemExit('iverilog/vvp not found')
    original = args.file.read_text()
    celled = step.transform(original)
    if step.restore(celled) != original or celled.count('psx_hold_cell hold_cell(') != 1 or not leaf_is_identity():
        raise SystemExit('FAIL: text identity')
    print(f'text identity: ok ({step.CELLS} cells; input sha256 {step.sha(original)[:16]}, celled {step.sha(celled)[:16]})', flush=True)
    parent = ROOT / 'build/sim/cue-hold-cells'
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='run-', dir=parent))
    print(work)
    good = work / 'celled'
    good.mkdir()
    (good / 'psx_cue_mount.sv').write_text(celled)
    build(good, good / 'psx_cue_mount.sv', iverilog)
    cases = [c for c in bench.CASES if not args.cases or c[0] in args.cases]
    results = [bench.run_case(good, iverilog, vvp, *case) for case in cases]
    print(f'celled: {sum(results)}/{len(results)} cases pass', flush=True)
    failed = not all(results)
    if not args.no_faults:
        for name in step.FAULTS:
            bad = work / f'fault_{name}'
            bad.mkdir()
            (bad / 'psx_cue_mount.sv').write_text(step.fault_text(celled, name))
            build(bad, bad / 'psx_cue_mount.sv', iverilog)
            caught = []
            for case in (c for c in bench.CASES if c[0] in FAULT_CASES):
                with contextlib.redirect_stdout(io.StringIO()):
                    try:
                        ok = bench.run_case(bad, iverilog, vvp, *case)
                    except subprocess.TimeoutExpired:
                        ok = False
                if not ok:
                    caught.append(case[0])
            print(f'fault {name}: {"caught by " + ", ".join(caught) if caught else "NOT CAUGHT"}', flush=True)
            failed |= not caught
    print('CUE-HOLD-CELLS ' + ('FAIL' if failed else 'PASS'))
    return failed


if __name__ == '__main__':
    sys.exit(main())
