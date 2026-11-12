#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""psx_save_exp1: the SPU RAM halfword crosses clk1x -> clk2x through a far-edge copy.

sp_q1 is loaded on a clk1x edge (the SPU RAM port's done) and read by clk2x
logic: sp_lo <= sp_q1 and the page write data {sp_q1, sp_lo}. Every clk1x
edge coincides with a clk2x edge, so the timing analyser checks hold with a
0 ns relation (D2 seed 12: -0.074 ns without the 150 ps reserve), although
the engine only reads the halfword two clk2x edges after it was loaded.

This step adds   always @(negedge clk2x) sp_qf <= sp_q1;   and lets the two
readers take sp_qf. The falling clk2x edge is a quarter clk1x period away
from every clk1x edge, so both new crossings (sp_q1 -> sp_qf, sp_qf -> the
rising-clk2x readers) have half a clk2x period of hold and of setup. No SDC
line is needed.

Exact, without any assumption about the engine: sp_q1 changes only at rising
clk1x edges, which are rising clk2x edges. At a rising clk2x edge P the old
readers saw the value sp_q1 held just before P. sp_qf was loaded at P minus
half a clk2x period, after the last possible change (P minus one clk2x
period or earlier), so it holds that same value. Every rising-clk2x reader
therefore reads the same word in the same clk2x cycle as before.
Cost: 16 registers on the falling clk2x edge.

Check: scripts/run_save_sp_far_edge.py.
Usage: prepare_save_sp_far_edge.py FILE --text-only OUT          (one psx_save_exp1.sv)
       prepare_save_sp_far_edge.py SOURCE --output OUTPUT [--seed N]   (frozen project snapshot)
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TAG = 'save-sp-far-edge'
DECL_OLD = "    reg [15:0] sp_q1 = 16'd0, sp_lo = 16'd0;\n"
COPY = '    always @(negedge clk2x) sp_qf <= sp_q1;\n'
DECL_NEW = (DECL_OLD +
            f'    // {TAG}: sp_q1 as the rising clk2x edge sees it, loaded on the falling edge between\n'
            "    reg [15:0] sp_qf = 16'd0;\n" + COPY)
LOW_OLD, LOW_NEW = 'sp_lo <= sp_q1;', 'sp_lo <= sp_qf;'
WORD_OLD, WORD_NEW = '{sp_q1, sp_lo}', '{sp_qf, sp_lo}'

# Planted faults: (old, new) on the transformed text.
FAULTS = {
    # the copy is taken on the rising edge: one clk2x cycle late
    'copy_rising': [(COPY, '    always @(posedge clk2x) sp_qf <= sp_q1;\n')],
    # the copy is taken on the falling clk1x edge: late in every other clk2x cycle
    'copy_clk1x': [(COPY, '    always @(negedge clk1x) sp_qf <= sp_q1;\n')],
    # the high halfword of the page word is the low one
    'high_from_low': [(WORD_NEW, '{sp_lo, sp_lo}')],
}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG}: expected exactly one match of: {old.strip()[:70]}')
    return text.replace(old, new)


def transform(text):
    """psx_save_exp1.sv (the repository file or a project's pinned copy) with the far-edge copy."""
    if TAG in text or 'sp_qf' in text:
        raise ValueError(f'{TAG}: already applied')
    # sp_q1: one declaration, one clk1x load, two clk2x readers; nothing on a falling edge yet.
    if len(re.findall(r'\bsp_q1\b', text)) != 4 or text.count('sp_q1 <= spuram_rdata;') != 1 or 'negedge' in text:
        raise ValueError(f'{TAG}: sp_q1 use changed')
    load = text[text.rindex('always @(', 0, text.index('sp_q1 <= spuram_rdata;')):text.index('sp_q1 <= spuram_rdata;')]
    if not load.startswith('always @(posedge clk1x)'):
        raise ValueError(f'{TAG}: sp_q1 is not loaded on rising clk1x')
    text = _once(text, DECL_OLD, DECL_NEW)
    text = _once(text, LOW_OLD, LOW_NEW)
    text = _once(text, WORD_OLD, WORD_NEW)
    if len(re.findall(r'\bsp_q1\b', text)) != 4 or len(re.findall(r'\bsp_qf\b', text)) != 4:
        raise ValueError(f'{TAG}: unexpected use count')
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
    rows = [r for r in manifest['quartus_sources'] if Path(r['path']).name == 'psx_save_exp1.sv']
    if len(rows) != 1:
        raise SystemExit(f'{TAG}: {source} does not compile one psx_save_exp1.sv')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    device = next(Path(r['path']) for r in before if Path(r['path']).name == 'psx_save_exp1.sv')
    if sha(device.read_text()) != rows[0]['sha256']:
        raise SystemExit(f'{TAG}: snapshot changed psx_save_exp1.sv')
    device.write_text(transform(device.read_text()))
    after = inputs(output)
    a, b = ({r['path']: r for r in rs} for rs in (after, before))
    if a.keys() != b.keys() or {p for p in b if a[p] != b[p]} != {str(device)}:
        raise SystemExit(f'{TAG}: expected only psx_save_exp1.sv to change')
    hold_guard(output, after)
    manifest['quartus_sources'] = after
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    if digest(Path(rows[0]['path'])) != rows[0]['sha256']:
        raise SystemExit(f'{TAG}: the source project changed')
    return device, seed, rows[0]['sha256']


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
            device, seed, old = prepare(args.source, args.output, args.seed)
            print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
            print(f'{TAG}: patched {device} (sha256 {old[:16]} -> {sha(device.read_text())})')
            print(f'{TAG}: 16 registers on falling clk2x (sp_qf); no SDC change')
    except ValueError as error:
        raise SystemExit(str(error))
