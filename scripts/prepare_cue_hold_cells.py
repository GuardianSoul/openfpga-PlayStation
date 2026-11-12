#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Identity LUTs on the cue reader's store data into its RAM lanes (a frozen project step).

Sign-off of candidate E2 seed 12 (cl-ssE2-signoff-20261111-s12) missed hold on
clk_74a at the fast 0 C corner by 0.001 ns:
  serv_bufreg2 dlo[21] -> psx_cue_ram_lane ram2 ... ram_block1a5~porta_datain_reg4
The rows within 0.1 ns are all the same bus: dlo[21] -> ram2 (-0.001 to
+0.015 ns, the port's data-in registers) and dlo[1] -> ram0 (+0.062, +0.064).
No address or write-enable row of the cue reader is within 0.1 ns. The path is
a register next to its M10K with no logic between (data delay 0.39 ns against
0.49 ns of clock skew and hold time).

This step puts rtl/pocket/psx_hold_cell.sv (one dont_touch identity LUT, the
cell prepare_main_hold_cells.py uses) on each of the 32 bits of the SERV CPU's
store data db_dat where the four RAM lanes take it (port A data-in only).
Placement moves with every fit, so the whole bus gets a cell, not only the
lanes that were short. The I/O registers keep reading db_dat directly. No
register, state, clock or timing exception is added: combinational identity;
cells are buffers.

Cost: 32 LUTs (16 to 32 ALMs, depending on how the fitter pairs them).
Check: scripts/run_cue_hold_cells.py.
Usage: prepare_cue_hold_cells.py FILE --text-only OUT              (one psx_cue_mount.sv)
       prepare_cue_hold_cells.py SOURCE --output OUTPUT [--seed N]   (frozen project snapshot)
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TAG = 'cue-hold-cells'
LEAF = 'a5533e51cf97214e5f113dab5ab7c26f907e48f4fa435af1ba8bb61884b2f46d'   # rtl/pocket/psx_hold_cell.sv
CELLS = 32
ANCHOR = '    psx_cue_ram_lane #(.FIRMWARE(FIRMWARE0)) ram0(.clk(clk),\n'
CELL = '        psx_hold_cell hold_cell(.i(db_dat[cue_hold_bit]), .o(cue_hold_dat[cue_hold_bit]));\n'
BLOCK = ('    // prepare_cue_hold_cells.py: identity LUTs on the CPU store data into the RAM lanes\n'
         '    wire [31:0] cue_hold_dat;\n'
         '    genvar cue_hold_bit;\n'
         '    generate for (cue_hold_bit = 0; cue_hold_bit < 32; cue_hold_bit = cue_hold_bit + 1) begin : cue_dat_hold\n'
         + CELL +
         '    end endgenerate\n')
LANES = [(f'.a_d(db_dat[{8*k + 7}:{8*k}])', f'.a_d(cue_hold_dat[{8*k + 7}:{8*k}])') for k in range(4)]

# Planted faults: (old, new) on the transformed text.
FAULTS = {
    # one cell inverts its bit (the bit that failed hold)
    'bit21_inverted': [(CELL, CELL.replace('.i(db_dat[cue_hold_bit])', '.i(db_dat[cue_hold_bit] ^ (cue_hold_bit == 21))'))],
    # two cells of one lane are crossed
    'bits_crossed': [(CELL, CELL.replace('.i(db_dat[cue_hold_bit])', '.i(db_dat[cue_hold_bit ^ (cue_hold_bit < 2)])'))],
}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG}: expected exactly one match of: {old.strip()[:70]}')
    return text.replace(old, new)


def transform(text):
    """psx_cue_mount.sv with the 32 cells between db_dat and the lanes' port A data."""
    if 'cue_hold_' in text or 'psx_hold_cell' in text:
        raise ValueError(f'{TAG}: already applied')
    # the four lanes are the only readers of db_dat as RAM data; one RAM lane module, four instances
    if len(re.findall(r'\.a_d\(', text)) != 4 or text.count('psx_cue_ram_lane #(.FIRMWARE(') != 4:
        raise ValueError(f'{TAG}: RAM lanes changed')
    text = _once(text, ANCHOR, BLOCK + ANCHOR)
    for old, new in LANES:
        text = _once(text, old, new)
    return text


def restore(text):
    """The inverse of transform (for the identity check): cells out, lanes back on db_dat."""
    text = _once(text, BLOCK, '')
    for old, new in LANES:
        text = _once(text, new, old)
    return text


def fault_text(text, fault):
    for old, new in FAULTS[fault]:
        text = _once(text, old, new)
    return text


def prepare(source, output, seed=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import digest, hold_guard, load_source, snapshot
    source, output = Path(source).resolve(), Path(output).resolve()
    manifest = load_source(source, source / 'fit-inputs.json')
    rows = [r for r in manifest['quartus_sources'] if Path(r['path']).name == 'psx_cue_mount.sv']
    cells = [r for r in manifest['quartus_sources'] if Path(r['path']).name == 'psx_hold_cell.sv']
    if len(rows) != 1:
        raise SystemExit(f'{TAG}: {source} does not compile one psx_cue_mount.sv')
    if len(cells) != 1 or cells[0]['sha256'] != LEAF:
        raise SystemExit(f'{TAG}: {source} does not compile the reviewed psx_hold_cell.sv')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    mount = next(Path(r['path']) for r in before if Path(r['path']).name == 'psx_cue_mount.sv')
    if sha(mount.read_text()) != rows[0]['sha256']:
        raise SystemExit(f'{TAG}: snapshot changed psx_cue_mount.sv')
    mount.write_text(transform(mount.read_text()))
    after = inputs(output)
    a, b = ({r['path']: r for r in rs} for rs in (after, before))
    if a.keys() != b.keys() or {p for p in b if a[p] != b[p]} != {str(mount)}:
        raise SystemExit(f'{TAG}: expected only psx_cue_mount.sv to change')
    hold_guard(output, after)
    manifest['quartus_sources'] = after
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    if digest(Path(rows[0]['path'])) != rows[0]['sha256']:
        raise SystemExit(f'{TAG}: the source project changed')
    return mount, seed, rows[0]['sha256']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--text-only', type=Path, metavar='OUT')
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
            mount, seed, old = prepare(args.source, args.output, args.seed)
            print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
            print(f'{TAG}: patched {mount} (sha256 {old[:16]} -> {sha(mount.read_text())})')
            print(f'{TAG}: {CELLS} identity LUTs (db_dat -> RAM lanes port A data); no register, no SDC change; fit-inputs.json rewritten')
    except ValueError as error:
        raise SystemExit(str(error))
