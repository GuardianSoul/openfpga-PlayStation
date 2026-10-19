#!/usr/bin/env python3
"""Guarded native368 experiment: parallel45/25 dividers with DSP subtraction.

No production selection. Preserve the exact selected reset-only ADSR/Gaussian
base, all six divider lanes and native completion clocks, and assigned150ps.
Physical map/fit/timing must use the Jenkins frozen interface.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from run_frozen_pocket_fit import load_source, snapshot, write_json
from tool_paths import ROOT

BASE_PROFILE = 'af290da675a90d95984cd1ae569ebca79a6bc5f48ec5612eb1095b6329588cc2'
RESULT_PROFILE = '9f92db911b368e837b62259e436f0d0b704de23a171808f3227f58a31edecb22'
GPU = '3698f4747ed3836832d84bb96fec57fc17e582a67c26899b9ec6338c80377a96'
LEAVES = {
    'psx_native_divider_pkg.vhd': '30d7939ee826a85b519fdb55295feeace721a74e124042b88d290a8ef5bd6ba6',
    'psx_native_divider_bank.vhd': 'd24ff148f4506ff0e7f5420c99e23b401e0ec923fa0d76d7e65a40e98c573e9d',
    'psx_native_subtract_dsp.sv': 'c883475cc60310759a198d44ade29664f092ef847df2d130f4412e491534f89d',
}


def inputs(project):
    # Load the initialized upstream-dependent scanner only for real projects.
    from measure_pocket_seeds import inputs as selected_inputs
    return selected_inputs(project)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def profile(project):
    """Bind all selected contents/settings, allowing only relocation and seed."""
    project = Path(project).resolve()
    rows, items = inputs(project), []
    for row in rows:
        path = Path(row['path'])
        if path.name in ('apf_top.qsf', 'apf_top.qpf'):
            continue
        raw = path.read_bytes().replace(str(project).encode(), b'<PROJECT>')
        items.append((row['assignment'], row.get('library', ''), path.name,
                      hashlib.sha256(raw).hexdigest()))
    text = (project / 'apf_top.qsf').read_text().replace(str(project), '<PROJECT>')
    settings = []
    relocatable = re.compile(
        r'^set_global_assignment -name (?:VHDL_FILE|SYSTEMVERILOG_FILE|VERILOG_FILE|'
        r'SDC_FILE|QIP_FILE|MISC_FILE|SEARCH_PATH|SEED)\b')
    for line in text.splitlines():
        if line.strip() and not relocatable.match(line):
            settings.append(line)
    contents = json.dumps(dict(inputs=sorted(items), settings=settings), sort_keys=True)
    return hashlib.sha256(contents.encode()).hexdigest(), len(rows)

def gpu_text(t):
    if hashlib.sha256(t.encode()).hexdigest() != GPU:
        raise ValueError('Unreviewed native GPU')
    marker = 'use work.pGPU.all;'
    assert t.count(marker) == 1
    t = t.replace(marker, marker + '\nuse work.psx_native_divider_pkg.all;')
    marker = '   type t_div_array is array(0 to 5) of div_type;'
    assert t.count(marker) == 1
    t = t.replace(marker, '   subtype t_div_array is native_div_array;\n'
                  '   signal div_response:t_div_array;\n'
                  '   signal divider_boundary:std_logic;')
    start = t.index('      idivider : entity work.divider')
    stop = t.index('      );', start) + len('      );')
    t = t[:start] + """      div_array(i).done<=div_response(i).done;
      div_array(i).quotient<=div_response(i).quotient;
      div_array(i).remainder<=div_response(i).remainder;""" + t[stop:]
    stop = t.index('   end generate;', start) + len('   end generate;')
    t = t[:stop] + """
   divider_boundary<=poly_div(0).start and not poly_div(3).start;
   native_divider_bank:entity work.psx_native_divider_bank port map(
      clk=>clk2x,boundary_batch=>divider_boundary,line_batch=>line_div(0).start,
      request=>div_array,response=>div_response);
""" + t[stop:]
    return t

def prepare(parent, manifest, output, seed=4):
    parent, manifest, output = [Path(p).resolve() for p in (parent, manifest, output)]
    if output.exists() or not output.is_relative_to(ROOT / 'build') or subprocess.run(
            ['git', 'check-ignore', '--quiet', str(output / 'fit-inputs.json')], cwd=ROOT).returncode:
        raise ValueError('Fresh ignored output required')
    before = load_source(parent, manifest)
    if profile(parent) != (BASE_PROFILE, 176):
        raise ValueError('Exact selected native368 ADSR/Gaussian profile required')
    support = ROOT / 'support/native-divider-dsp'
    if any(digest(support / n) != sha for n, sha in LEAVES.items()):
        raise ValueError('Unreviewed native divider implementation')
    snapshot(parent, manifest, output, seed)
    gpu_rows = [r for r in inputs(output) if Path(r['path']).name == 'gpu.vhd']
    if len(gpu_rows) != 1:
        raise ValueError('One native GPU required')
    gpu = Path(gpu_rows[0]['path'])
    gpu.write_text(gpu_text(gpu.read_text()))
    leaf = output / 'native-divider-dsp'
    leaf.mkdir()
    qsf = output / 'apf_top.qsf'
    text = qsf.read_text()
    for name in LEAVES:
        dest = leaf / name
        shutil.copy2(support / name, dest)
        kind = 'SYSTEMVERILOG_FILE' if name.endswith('.sv') else 'VHDL_FILE'
        text += '\nset_global_assignment -name ' + kind + ' "' + str(dest) + '"' + (
            ' -library work' if kind == 'VHDL_FILE' else '') + '\n'
    qsf.write_text(text)
    rows = inputs(output)
    if profile(output) != (RESULT_PROFILE, 179):
        raise ValueError('Unexpected composed hardware/settings profile')
    target = json.loads((output / 'fit-inputs.json').read_text())
    target.update(quartus_sources=rows, project_sha256={
        suffix: digest(output / ('apf_top' + suffix)) for suffix in ('.qsf', '.qpf')})
    write_json(output / 'fit-inputs.json', target)
    load_source(output, output / 'fit-inputs.json')
    if load_source(parent, manifest) != before:
        raise ValueError('Parent changed during preparation')
    write_json(leaf / 'manifest.json', dict(
        selected=False, base_profile=BASE_PROFILE, result_profile=RESULT_PROFILE,
        native_lanes=6, numerator_bits=45, denominator_bits=25, difference_bits=26,
        added_guest_cycles=0, extra_cpu_hold_ns=.15, source_inputs=rows))
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('parent', type=Path)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=4)
    args = parser.parse_args()
    prepare(args.parent, args.manifest, args.output, args.seed)
    print('Prepared guarded native368 DSP experiment; no physical fit or production selection')


if __name__ == '__main__':
    main()
