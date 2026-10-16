#!/usr/bin/env python3
"""Prepare a whole GPU stall-buffer handoff routing floor before map.

No RTL, clocks, cycles, exceptions or timing uncertainty change. The selected
source and mapped bank geometry are deliberately narrow. A new fit and strict
timing qualification are required; this is not a repair of a completed fit.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest
from tool_paths import ROOT

PIXEL_SHA = '5a54e80fffd05dfcd2553a0f78691fe8c730d5f37f16054ecbf75d54e28dbe2d'


def prepare_project(directory, expected_top_sha256):
    directory = Path(directory).resolve()
    work = directory / 'gpu-stage-route'
    if (not directory.is_relative_to(ROOT / 'build') or
            subprocess.run(['git', 'check-ignore', '--quiet', str(work / 'manifest.json')],
                           cwd=ROOT).returncode):
        raise ValueError('Fresh ignored build project required')
    if any((directory / n).exists() for n in ('db', 'incremental_db', 'output_files', 'gpu-stage-route')):
        raise ValueError('GPU stage routing must be prepared before map/fit')
    qsf, core = directory / 'apf_top.qsf', directory / 'core_top.sv'
    q = qsf.read_text()
    selected = assignments(directory, q)
    if (digest(core) != expected_top_sha256 or
            [(k, p) for k, p in selected if p.name == 'core_top.sv'] != [('SYSTEMVERILOG_FILE', core)]):
        raise ValueError('GPU route top identity differs')
    hold = [p for k, p in selected if k == 'SDC_FILE' and p.name == 'pocket_cpu_hold.sdc']
    if len(hold) != 1 or not hold[0].read_text().startswith('set pocket_cpu_hold_ns 0.15\n'):
        raise ValueError('GPU routing requires the unchanged150ps hold reserve')
    pixels = [p for k, p in selected if k == 'VHDL_FILE' and p.name == 'gpu_pixelpipeline.vhd']
    if len(pixels) != 1 or digest(pixels[0]) != PIXEL_SHA:
        raise ValueError('GPU pipeline source identity differs')
    source = ROOT / 'scripts/pocket_gpu_stage_route.tcl'
    protected = {str(p): digest(p) for p in (qsf, core, hold[0], pixels[0], source)}
    if any(digest(Path(p)) != sha for p, sha in protected.items()):
        raise ValueError('GPU route project changed during preparation')
    work.mkdir()
    scope = work / 'route.sdc'
    shutil.copyfile(source, scope)
    qsf.write_text(q + f'\nset_global_assignment -name SDC_FILE "{scope}"\n')
    report = dict(original=protected, scope=dict(path=str(scope), sha256=digest(scope)),
                  pixel_source_sha256=PIXEL_SHA, required_CPU_hold_ps=150,
                  logical_bits_per_bank=79, forwarded_fields=13, minimum_net_delay_ns=0.5,
                  scope_description='Complete stageS to stage0 handoff; physical replicas included',
                  later_texture_address_bank_excluded=True,
                  RTL_clocks_phases_features_and_guest_cycles_unchanged=True,
                  ordinary_setup_and_hold_retained=True, per_bit_RTL_padding_added=False,
                  preparer_sha256=digest(Path(__file__)), opt_in=True,
                  physical_timing_verified=False, hardware_verified=False, production_selected=False)
    (work / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--expected-top-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_project(args.project, args.expected_top_sha256), indent=2))
