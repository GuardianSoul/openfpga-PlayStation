#!/usr/bin/env python3
"""Add guarded minimum-delay requests to the measured Native hold paths.

No RTL, clock, cycle, exception or ordinary setup/hold requirement changes.
The existing 150 ps CPU reserve remains selected. Requires a fresh full fit.
"""
import argparse
import json
import math
from pathlib import Path
import re
import shutil
import subprocess

from measure_pocket_seeds import inputs
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest
from tool_paths import ROOT


REVIEWED = {
    'dma.vhd': '091d5267cd99d62882671fa2c70692d9fe46ceadfb7d0fbee6e2ba0dd167937a',
    'psx_sdram_ctrl.sv': 'e96f00a8a26ab2c47b77ba6ed8a066f7653e1215e83872b2aa1d417a83753adf',
}


def prepare_project(directory, expected_top_sha256, *, dma_ns=.5, sdr_ns=.4):
    if not (math.isfinite(dma_ns) and .4 <= dma_ns <= 1 and
            math.isfinite(sdr_ns) and .2 <= sdr_ns <= 1):
        raise ValueError('Require finite DMA .4..1 ns / SDRAM .2..1 ns minimum')
    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT/'build') or subprocess.run(
            ['git', 'check-ignore', '--quiet', str(directory/'native-hold-route/manifest.json')],
            cwd=ROOT).returncode:
        raise ValueError('Fresh ignored build project required')
    if any((directory/n).exists() for n in ('db', 'incremental_db', 'output_files', 'native-hold-route')):
        raise ValueError('Native hold routing must precede map/fit')
    qsf, core = directory/'apf_top.qsf', directory/'core_top.sv'
    q = qsf.read_text()
    selected = assignments(directory, q)
    if digest(core) != expected_top_sha256 or [p for k, p in selected
            if k == 'SYSTEMVERILOG_FILE' and p.name == 'core_top.sv'] != [core]:
        raise ValueError('Expected identity-checked canonical top')
    for name, expected in REVIEWED.items():
        paths = {p for _, p in selected if p.name == name}
        if len(paths) != 1 or digest(next(iter(paths))) != expected:
            raise ValueError('Unreviewed Native hold source '+name)
    before = inputs(directory)
    reserves = []
    for row in before:
        if row['assignment'] in ('SDC_FILE', 'SDC_SOURCE', 'PROJECT'):
            reserves += re.findall(r'^\s*set pocket_cpu_hold_ns\s+(\S+)\s*$',
                                   Path(row['path']).read_text(), re.M)
    if len(reserves) != 1 or float(reserves[0]) != .15:
        raise ValueError('Expected one literal retained150ps CPU reserve')
    work = directory/'native-hold-route'
    work.mkdir()
    scope = work/'scope.tcl'
    shutil.copyfile(ROOT/'scripts/pocket_native_hold_scope.tcl', scope)
    config = work/'pocket_native_hold_route.sdc'
    config.write_text(f'set native_hold_dma_ns {dma_ns!r}\nset native_hold_sdr_ns {sdr_ns!r}\nsource "{scope}"\n')
    if any(digest(Path(r['path'])) != r['sha256'] for r in before):
        raise ValueError('Native hold inputs changed during preparation')
    qsf.write_text(q+f'\nset_global_assignment -name SDC_FILE "{config}"\n')
    result = dict(original_inputs=before, minimum_ns=dict(dma=dma_ns, sdr=sdr_ns),
        selected_bits=dict(dma=[11], sdr=list(range(32))), CPU_hold_reserve_ns=.15,
        added=[dict(path=str(p), sha256=digest(p)) for p in (scope, config)],
        preparer_sha256=digest(Path(__file__)), RTL_clocks_cycles_unchanged=True,
        original_setup_hold_retained=True, exceptions_added=False,
        fit_verified=False, timing_verified=False, production_selected=False,
        hardware_verified=False)
    (work/'manifest.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--expected-top-sha256', required=True)
    parser.add_argument('--dma-ns', type=float, default=.5)
    parser.add_argument('--sdr-ns', type=float, default=.4)
    args = parser.parse_args()
    print(json.dumps(prepare_project(args.project, args.expected_top_sha256,
                                    dma_ns=args.dma_ns, sdr_ns=args.sdr_ns), indent=2))
