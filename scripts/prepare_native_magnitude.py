#!/usr/bin/env python3
"""Reproduce experimental native45 GPU producer-magnitude factoring on Fill/B6.

All six divider lanes and native completion clocks remain. No production
selection or timing claim; run physical tools through the Jenkins queue.
"""
import argparse
import json
from pathlib import Path
import subprocess

from prepare_native_divider_dsp import profile
from run_frozen_pocket_fit import digest, load_source, require, snapshot, write_json
from tool_paths import ROOT

ASSETS = ROOT / 'support/native-producer-magnitude'
MANIFEST = '5dd05fc31556f2dff5bb4cca3919a2d11c2399f71c36bb1361e5bbb5cca774e4'
BASE = ('7f684e5b294f2b33f421b51bba23a2d3e2448292f32d9b2d57cc08cc66c50a63', 176)
RESULT = ('b637ab35378e85b6b80f05bf9fd811b4bd50549a7bf0ac2a9f99f4a93bced5d6', 178)


def prepare(source, output, seed=3):
    source, output = Path(source).resolve(), Path(output).resolve()
    require(not output.exists() and output.is_relative_to(ROOT / 'build') and
            subprocess.run(['git', 'check-ignore', '--quiet', str(output / 'fit-inputs.json')],
                           cwd=ROOT).returncode == 0, 'Fresh ignored output required')
    parent = load_source(source, source / 'fit-inputs.json')
    require(profile(source) == BASE, 'Exact selected Fill/B6 source and settings profile required')
    manifest = ASSETS / 'manifest.json'
    require(digest(manifest) == MANIFEST, 'Unreviewed producer-magnitude manifest')
    assets = json.loads(manifest.read_text())
    require(tuple(assets['base_profile']) == BASE and tuple(assets['result_profile']) == RESULT,
            'Producer-magnitude profile mismatch')
    protected = {str(manifest): MANIFEST}
    for row in assets['hooks']:
        protected[str(ASSETS / row['patch'])] = row['patch_sha256']
    for row in assets['leaves']:
        protected[str(ASSETS / row['name'])] = row['sha256']
    require(all(digest(p) == h for p, h in protected.items()), 'Changed producer-magnitude support')
    target = snapshot(source, source / 'fit-inputs.json', output, seed)
    for row in assets['hooks']:
        selected = [r for r in target['quartus_sources'] if Path(r['path']).name == row['name']]
        require(len(selected) == 1, 'One selected GPU source required: ' + row['name'])
        path = Path(selected[0]['path'])
        require(digest(path) == row['input_sha256'], 'Changed producer source: ' + row['name'])
        subprocess.run(['patch', '--batch', '--forward', '--fuzz=0', str(path),
                        str(ASSETS / row['patch'])], check=True, capture_output=True, text=True)
        require(digest(path) == row['output_sha256'], 'Unexpected producer result: ' + row['name'])
    additions = []
    for row in assets['leaves']:
        path = output / row['name']
        require(not path.exists(), 'Fresh divider leaf required')
        path.write_bytes((ASSETS / row['name']).read_bytes())
        additions.append('set_global_assignment -name VHDL_FILE "' + str(path) + '" -library work\n')
    qsf = output / 'apf_top.qsf'
    qsf.write_text(qsf.read_text() + '\n' + ''.join(additions))
    require(profile(output) == RESULT, 'Unexpected composed producer hardware/settings')
    from measure_pocket_seeds import inputs
    target.update(quartus_sources=inputs(output), project_sha256={
        suffix: digest(output / ('apf_top' + suffix)) for suffix in ('.qsf', '.qpf')})
    write_json(output / 'fit-inputs.json', target)
    require(load_source(output, output / 'fit-inputs.json') == target, 'Prepared source drift')
    require(load_source(source, source / 'fit-inputs.json') == parent and
            all(digest(p) == h for p, h in protected.items()), 'Original inputs/support changed')
    write_json(output / 'producer-magnitude.json', dict(
        selected=False, fit_verified=False, timing_verified=False, hardware_verified=False,
        base_profile=BASE, result_profile=RESULT, protected_support_sha256=protected,
        native_lanes=6, numerator_bits=45, denominator_bits=25, added_guest_cycles=0,
        added_DSP=0, added_RAM=0, extra_cpu_hold_ns=.15, preparer_sha256=digest(__file__),
        source_inputs=parent['quartus_sources'], captured_inputs=target['quartus_sources']))
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--seed', type=int, default=3)
    args = parser.parse_args()
    prepare(args.source, args.output, args.seed)
    print('Prepared native producer-magnitude experiment; fit/timing unqualified')
