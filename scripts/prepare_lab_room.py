#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""lab-room: structural LAB savings on a frozen project snapshot (docs/save-states-finish/lab-room.md).

Each part is a text transform of one compiled source, with its own module, planted faults and
lockstep runner:
  poly   lab_room_poly.py   gpu_poly edge steps read from the held divider quotients
                            (run_lab_room_poly.py, run_lab_room_gpu.py)
  line   lab_room_line.py   gpu_line steps read from the held divider quotients (run_lab_room_gpu.py)
  pixel  lab_room_pixel.py  pixel pipeline stage0..4 position/background delay in an MLAB ring
                            (run_lab_room_gpu.py; measured worse, not in the default parts)
  xa     lab_room_xa.py     cd_xa ADPCM history in 16 bits (run_lab_room_xa.py)

Usage: prepare_lab_room.py SOURCE --output OUTPUT [--parts poly,...] [--seed N]
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import lab_room_exe
import lab_room_line
import lab_room_pixel
import lab_room_poly
import lab_room_polyflat
import lab_room_xa

TAG = 'lab-room'
# part -> (compiled file name, QSF assignment kind, module with transform())
PARTS = {
    'poly': ('gpu_poly.vhd', 'VHDL_FILE', lab_room_poly),
    'polyflat': ('gpu_poly.vhd', 'VHDL_FILE', lab_room_polyflat),   # stacked on poly
    'line': ('gpu_line.vhd', 'VHDL_FILE', lab_room_line),
    'pixel': ('gpu_pixelpipeline.vhd', 'VHDL_FILE', lab_room_pixel),
    'xa': ('cd_xa.vhd', 'VHDL_FILE', lab_room_xa),
    'exe': ('memorymux.vhd', 'VHDL_FILE', lab_room_exe),
}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def compiled(project, name, kind):
    qsf = (project / 'apf_top.qsf').read_text()
    hits = sorted({l.split('"')[1] for l in qsf.splitlines()
                   if kind in l and l.count('"') >= 2 and l.split('"')[1].endswith('/' + name)})
    if len(hits) != 1 or not Path(hits[0]).is_relative_to(project):
        raise ValueError(f'{TAG}: expected one compiled {name} inside the snapshot, found {hits}')
    return Path(hits[0])


def prepare(source, output, parts, seed=None):
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import hold_guard, load_source, snapshot
    source, output = Path(source).resolve(), Path(output).resolve()
    load_source(source, source / 'fit-inputs.json')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    changed = {}
    for part in parts:
        name, kind, module = PARTS[part]
        path = compiled(output, name, kind)
        old = path.read_text()
        path.write_text(module.transform(old))
        first, _, done = changed.get(str(path), (sha(old), None, None))   # stacked parts keep the original
        changed[str(path)] = (first, sha(path.read_text()), f'{done}+{part}' if done else part)
    current = inputs(output)
    a, b = ({r['path']: r for r in rows} for rows in (current, before))
    if a.keys() != b.keys() or {p for p in b if a[p] != b[p]} != set(changed):
        raise ValueError(f'{TAG}: unexpected input changes')
    hold_guard(output, current)
    manifest['quartus_sources'] = current
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    report = dict(source=str(source), seed=seed, parts=list(parts),
                  changed={p: dict(before=o, after=n, part=k) for p, (o, n, k) in sorted(changed.items())})
    (output / f'{TAG}.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--parts', default='poly,line,xa')
    parser.add_argument('--seed', type=int)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.source, args.output, args.parts.split(','), args.seed), indent=2))
    except ValueError as error:
        raise SystemExit(str(error))
