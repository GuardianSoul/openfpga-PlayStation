#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Base trims batch A on the full-audio save-state graph (a frozen project step).

The four 0.5.6 trims that paid in psx-fit #552 (docs/base-savings-survey-056.md,
-3 LABs then) and still apply: io_pollcnt (pad controller poll count from the
heartbeat), spu_adsrq (no adsrSetting copy), spu_noise (noise threshold as a
mask test), cd_copycount (copy ends on copyReadAddr). The other two kept trims
are already in the recipe in another form: cpu_div0 as prepare_cpu_div_shared,
cd_sectorword as prepare_cd_sector_word.

The patches in rtl/pocket/base_trims_a are the 0.5.6 patches re-based onto this
graph's files: each makes exactly the same added/removed lines (checked when
they were made). They apply with --fuzz=0, in this order, to the files the
project compiles. Checks on the re-based patches (run_base_trim_audio.py and
run_base_trim_cd.py with BASE_TRIM_PATCHES=rtl/pocket/base_trims_a) are
recorded in the save thread's notes.
Usage: prepare_base_trims_a.py SOURCE --output OUT
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / 'rtl/pocket/base_trims_a'
ORDER = [('io_pollcnt', 'io_pad_controller.v'), ('spu_adsrq', 'spu.vhd'),
         ('spu_noise', 'spu.vhd'), ('spu_envstep', 'spu.vhd'),
         ('cd_copycount', 'cd_top.vhd')]


def compiled(project, name):
    qsf = (project / 'apf_top.qsf').read_text()
    hits = sorted({l.split('"')[1] for l in qsf.splitlines() if 'set_global_assignment' in l and f'/{name}"' in l})
    if len(hits) != 1:
        raise ValueError(f'expected one compiled {name}, found {hits}')
    return Path(hits[0])


def prepare(source, output):
    from run_frozen_pocket_fit import snapshot, hold_guard
    from measure_pocket_seeds import inputs
    source, output = Path(source).resolve(), Path(output).resolve()
    result = snapshot(source, source / 'fit-inputs.json', output, 2)
    touched = set()
    for name, src in ORDER:
        target = compiled(output, src)
        if not target.is_relative_to(output):
            raise ValueError(f'{src} is outside the snapshot')
        r = subprocess.run(['patch', '--fuzz=0', '--no-backup-if-mismatch', '-s', str(target),
                            '-i', str(PATCHES / f'{name}.patch')], capture_output=True, text=True)
        if r.returncode:
            raise ValueError(f'{name} does not apply: {r.stdout}{r.stderr}')
        touched.add(str(target))
    current = inputs(output)
    changed = {a['path'] for a, b in zip(result['quartus_sources'], current) if a != b}
    if len(current) != len(result['quartus_sources']) or changed != touched:
        raise ValueError('Base trims changed other selected inputs')
    hold_guard(output, current)
    result['quartus_sources'] = current
    (output / 'fit-inputs.json').write_text(json.dumps(result, indent=2) + '\n')
    report = dict(source=str(source), trims=[n for n, _ in ORDER], changed=sorted(changed))
    (output / 'base-trims-a.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    print(prepare(a.source, a.output))
