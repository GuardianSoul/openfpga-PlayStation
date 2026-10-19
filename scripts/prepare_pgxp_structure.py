#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reproduce an experimental PGXP area graph from its qualified native input.

Preserves the original preparer's complete native source/feature/150 ps guard.
Profiles are source experiments, not production defaults or fit/timing claims.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

from prepare_pgxp import (ROOT, PROJECT_TOKEN, normalized_text, prepare_project,
                          selected, text_digest)
from measure_pocket_seeds import inputs
from prepare_resource_targets import digest

ASSETS = ROOT / 'rtl/pocket/pgxp_structure'
MAGNITUDE_ASSETS = ROOT / 'rtl/pocket/pgxp_magnitude'


def spu_instance(project):
    text = selected(project, 'psx_top.vhd').read_text()
    start = text.index('   ispu : entity work.spu')
    return text[start:text.index('   );', start) + len('   );')]


def prepare(source, output, profile, spu_gaussian=False, producer_magnitude=False):
    if profile not in ('gpu', 'gpu-masked-shadow'):
        raise ValueError('Select a qualified structural profile')
    source, output = Path(source).resolve(), Path(output).resolve()
    asset_path = ASSETS / 'manifest.json'
    asset_hash = digest(asset_path)
    assets = json.loads(asset_path.read_text())
    if digest(ROOT / 'rtl/pocket/pgxp_hooks/manifest.json') != assets['baseline_hook_manifest_sha256']:
        raise ValueError('Unqualified baseline PGXP hooks')
    for row in assets['hooks']:
        if digest(ASSETS / row['patch']) != row['patch_sha256']:
            raise ValueError('Changed structural hook: ' + row['name'])
    bank = assets['bank']
    if digest(ROOT / 'rtl/pocket' / bank['name']) != bank['sha256']:
        raise ValueError('Changed parallel divider bank')
    magnitude_hash, magnitude_hooks, magnitude_scope = None, [], ''
    if producer_magnitude:
        path = MAGNITUDE_ASSETS / 'manifest.json'
        magnitude_hash = digest(path)
        extra = json.loads(path.read_text())
        if extra['baseline_structure_manifest_sha256'] != asset_hash:
            raise ValueError('Unqualified magnitude baseline')
        magnitude_hooks = extra['hooks']
        magnitude_scope = extra['scope']
        for row in magnitude_hooks:
            if digest(MAGNITUDE_ASSETS / row['patch']) != row['patch_sha256']:
                raise ValueError('Changed magnitude hook: ' + row['name'])
    original_spu = spu_instance(source)
    baseline = prepare_project(source, output)
    changes = []
    for row in assets['hooks']:
        if row['profile'] == 'masked-shadow' and profile != 'gpu-masked-shadow':
            continue
        path = selected(output, row['name'])
        text = normalized_text(path, output)
        if text_digest(text) != row['input_sha256']:
            raise ValueError('Unqualified structural input: ' + row['name'])
        path.write_text(text)
        subprocess.run(['patch', '--batch', '--forward', '--fuzz=0', str(path),
                        str(ASSETS / row['patch'])], cwd=output, check=True,
                       capture_output=True, text=True)
        if digest(path) != row['output_sha256']:
            raise ValueError('Unexpected structural output: ' + row['name'])
        path.write_text(path.read_text().replace(PROJECT_TOKEN, str(output)))
        changes.append(dict(row, path=str(path), prepared_sha256=digest(path)))
    path = output / 'pgxp' / bank['name']
    shutil.copyfile(ROOT / 'rtl/pocket' / bank['name'], path)
    if digest(path) != bank['sha256']:
        raise ValueError('Divider bank changed during capture')
    qsf = output / 'apf_top.qsf'
    qsf.write_text(qsf.read_text() + '\nset_global_assignment -name VHDL_FILE "' +
                   str(path) + '" -library work\n')
    spu = None
    if spu_gaussian:
        from prepare_spu_fir_width import spu_fir_width_text, EXPECTED
        from prepare_spu_gaussian_mac import gaussian_text, COMBINED_OUTPUT, WRAPPER, ROM
        path = selected(output, 'spu.vhd')
        wrapper = ROOT / 'rtl/pocket/psx_spu_gaussian_mac.sv'
        if (digest(path) != EXPECTED or digest(selected(output, 'spu_gauss.vhd')) != ROM or
                digest(wrapper) != WRAPPER or spu_instance(output) != original_spu):
            raise ValueError('Unqualified SPU source/ROM/wrapper/reset wiring')
        path.write_text(gaussian_text(spu_fir_width_text(path.read_text()), with_adsr=True))
        if digest(path) != COMBINED_OUTPUT:
            raise ValueError('Unexpected FIR/ADSR/Gaussian composition')
        new = output / 'pgxp' / wrapper.name
        shutil.copyfile(wrapper, new)
        if digest(new) != WRAPPER:
            raise ValueError('SPU wrapper changed during capture')
        qsf.write_text(qsf.read_text() + '\nset_global_assignment -name SYSTEMVERILOG_FILE "' +
                       str(new) + '" -library work\n')
        spu = dict(input_sha256=EXPECTED, output_sha256=COMBINED_OUTPUT,
                   wrapper_sha256=WRAPPER, rom_sha256=ROM, reset_wiring_unchanged=True)
    for row in magnitude_hooks:
        path = selected(output, row['name'])
        text = normalized_text(path, output)
        if text_digest(text) != row['input_sha256']:
            raise ValueError('Unqualified magnitude input: ' + row['name'])
        path.write_text(text)
        subprocess.run(['patch', '--batch', '--forward', '--fuzz=0', str(path),
                        str(MAGNITUDE_ASSETS / row['patch'])], cwd=output,
                       check=True, capture_output=True, text=True)
        if digest(path) != row['output_sha256']:
            raise ValueError('Unexpected magnitude output: ' + row['name'])
        path.write_text(path.read_text().replace(PROJECT_TOKEN, str(output)))
        changes.append(dict(row, transform='producer-magnitude', path=str(path),
                            prepared_sha256=digest(path)))
    if producer_magnitude and digest(MAGNITUDE_ASSETS / 'manifest.json') != magnitude_hash:
        raise ValueError('Magnitude assets changed during preparation')
    if digest(asset_path) != asset_hash or inputs(source) != baseline['original_inputs']:
        raise ValueError('Preparation source changed')
    target = dict(top='apf_top', directory=str(output), quartus_sources=inputs(output),
                  project_sha256={s: digest(output / ('apf_top' + s)) for s in ('.qsf', '.qpf')},
                  fixed_ports={}, physical_clock_ports=['clk_74a', 'clk_74b'])
    report = dict(schema=1, kind='PGXP structural experiment preparation', profile=profile,
                  producer_magnitude=producer_magnitude, magnitude_assets_sha256=magnitude_hash,
                  baseline_preparation_sha256=digest(output / 'pgxp-preparation.json'),
                  structural_assets_sha256=asset_hash, changes=changes,
                  bank=dict(bank, prepared_sha256=digest(selected(output, bank['name']))), spu=spu,
                  target=target, extra_cpu_hold_ns=.15, fit_verified=False,
                  timing_verified=False, hardware_verified=False, selected=False,
                  scope=assets['scope'] + (' ' + magnitude_scope if producer_magnitude else ''))
    (output / 'timing-inputs.json').write_text(json.dumps(target, indent=2) + '\n')
    (output / 'pgxp-structure.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', choices=('gpu', 'gpu-masked-shadow'), required=True)
    parser.add_argument('--spu-gaussian', action='store_true',
                        help='Compose the exact guarded FIR/ADSR/Gaussian source')
    parser.add_argument('--producer-magnitude', action='store_true',
                        help='Factor numerator magnitude arithmetic in the existing producer stages')
    args = parser.parse_args()
    report = prepare(args.input, args.output, args.profile, args.spu_gaussian,
                     args.producer_magnitude)
    print(f'Prepared {report["profile"]}: {len(report["target"]["quartus_sources"])} input roles; '
          'fit and timing unverified')


if __name__ == '__main__':
    main()
