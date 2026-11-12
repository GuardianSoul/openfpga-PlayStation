#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Swap Discs read-back: reads of the Swap Discs setting (bridge 0x50000044) return the core's disc
setting, so the Pocket's menu follows a disc switch made by a Memory load.

Why (hardware, 0.5.9-test36, owner, 2026-11-12 13:53 UTC): with the setting not `writeonly` the
Pocket reads it back from the core; a core that answers 0 (no read path) makes the menu snap back
to Disc 1 and the swap cannot be picked. So the read path and dropping `writeonly` go together.

Hardware (a frozen project step; core_top.sv only, no RAM, no clock crossing, no SDC line):
  disc_setting (clk_74a, 3 bits) is already the latch the swap logic follows: a menu write sets it
  on the write's clock edge; the save routine's DISC_SET (a Memory load on another disc) sets it
  through its synchronised toggle. The step moves its declaration above the bridge read
  multiplexer (it was declared after it) and adds one read case:
      32'h50000044: bridge_rd_data = {29'b0, disc_setting};
  So a menu pick reads back as the new value from the next clock on (never the old one), a Memory
  load's disc reads back once DISC_SET lands, and every other address reads as before.
  Cost: one 3-bit field in the read multiplexer (an address compare that already exists for the
  write, a 3-wide AND-OR).

Package: the Swap Discs variable drops `writeonly` ONLY together with this step
(package_pocket_early.py --disc-readback).

Check: scripts/run_save_disc_readback.py (bench of the transformed text, planted faults).
Usage: prepare_save_disc_readback.py SOURCE --output OUTPUT [--seed N]   (frozen project snapshot)
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'save-disc-readback'
DECL = '    reg [2:0] disc_setting=0;\n'
SERVICE = '    reg [31:0] service_read;\n'
CASE_OLD = '''\
        else case (bridge_addr)
            32'h10000000: bridge_rd_data={28'b0,checking,failed,passed,mem_ready74[1]};
'''
CASE_NEW = '''\
        else case (bridge_addr)
            32'h10000000: bridge_rd_data={28'b0,checking,failed,passed,mem_ready74[1]};
            32'h50000044: bridge_rd_data={29'b0,disc_setting};   // save-disc-readback: Swap Discs reads back the disc setting
'''


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG}: expected one match, found {text.count(old)}: {old.strip()[:90]!r}')
    return text.replace(old, new)


def core_top(text):
    if TAG in text:
        raise ValueError(f'{TAG}: core_top.sv already has the read-back')
    if text.index(SERVICE) > text.index(DECL):
        raise ValueError(f'{TAG}: disc_setting is already declared before the read multiplexer')
    text = once(text, DECL, '')
    text = once(text, SERVICE, DECL.rstrip('\n') + '   // save-disc-readback: declared before the bridge read multiplexer\n' + SERVICE)
    return once(text, CASE_OLD, CASE_NEW)


# Planted faults for the bench (never a fitted input): name -> (old, new) on the step's text
FAULTS = {
    # test36: no read path, the menu reads 0
    'reads-zero': ("32'h50000044: bridge_rd_data={29'b0,disc_setting};", "32'h50000044: bridge_rd_data=32'b0;"),
    # the read-back ignores the routine's DISC_SET (a Memory load's disc is not shown)
    'menu-only': ('        else if (save_disc_set_sync[1]!=save_disc_set_seen) disc_setting<=save_disc_set;\n', ''),
    # the read sits at the neighbouring setting's address
    'wrong-address': ("32'h50000044: bridge_rd_data={29'b0,disc_setting};", "32'h50000048: bridge_rd_data={29'b0,disc_setting};"),
    # only bit 0 reads back (Disc 3 reads as Disc 1)
    'one-bit': ("{29'b0,disc_setting}", "{31'b0,disc_setting[0]}"),
}


def compiled(project, name, kind):
    qsf = (project / 'apf_top.qsf').read_text()
    hits = sorted({l.split('"')[1] for l in qsf.splitlines() if kind in l and l.count('"') >= 2 and l.split('"')[1].endswith('/' + name)})
    if len(hits) != 1 or not Path(hits[0]).is_relative_to(project):
        raise ValueError(f'{TAG}: expected one compiled {name} inside the snapshot, found {hits}')
    return Path(hits[0])


def prepare(source, output, seed=None):
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import hold_guard, load_source, snapshot
    source, output = Path(source).resolve(), Path(output).resolve()
    load_source(source, source / 'fit-inputs.json')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    path = compiled(output, 'core_top.sv', 'SYSTEMVERILOG_FILE')
    old = path.read_text()
    path.write_text(core_top(old))
    changed = {str(path): (sha(old), sha(path.read_text()))}
    current = inputs(output)
    a, b = ({r['path']: r for r in rows} for rows in (current, before))
    if a.keys() != b.keys() or {p for p in b if a[p] != b[p]} != set(changed):
        raise ValueError(f'{TAG}: unexpected input changes')
    hold_guard(output, current)
    manifest['quartus_sources'] = current
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    report = dict(source=str(source), seed=seed, changed={p: dict(before=o, after=n) for p, (o, n) in sorted(changed.items())})
    (output / f'{TAG}.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.source, args.output, args.seed), indent=2))
    except ValueError as error:
        raise SystemExit(str(error))
