#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Optional source-bound FIR width reduction; no coefficient/stage/clock change.

Preserves the selected MiSTer lightweight five-tap reverb filter. This is not
an exact39tap resampler, audio fix or a full timing-qualified release.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from measure_pocket_seeds import inputs
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import UPSTREAM, digest
from tool_paths import ROOT

EXPECTED = '169813eeab5a521bc897cc7a5c64923571a4fd52f2454ab49758ce27d11361be'
OUTPUT = 'b75586c8b309299bd16e5f558760d69aa21db738a15de35b2d9af5f153eda256'
HOLD = '6a0f9b621fb4a05f948671fb95488db90abc68759637045c15441eb14399ee53'
# the reviewed hold scope; the second (48781c9) also accepts the --vram-full-rate
# chip clock general[3] and leaves it out of the margin, the same 9 CPU pairs
HOLD_SCOPE = ('90ae3ebe6be262a6198e2f9200e73a5d0806a8bbaacfdfa1cdf2a7be936636df',
              '9b60631e6477c2b59de3da2e29730675fe99292572f4c0c51db721814536e189',
              '23f4cd8c9a3c94cf1fe30e845b52244517b7982d3c333168fab0c568b2ca5987')   # + --vram-x3 chip clock at the clk3x period
LICENSE = '8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643'

def require(ok, message):
    if not ok:
        raise ValueError(message)

def _transform(text):
    def sub(before, after):
        nonlocal text
        require(text.count(before) == 1,
                'Unexpected FIR source expression: ' + before)
        text = text.replace(before, after)

    for side in ('L', 'R'):
        sub(f'signal reverbPartA_{side}          : signed(24 downto 0)',
            f'signal reverbPartA_{side}          : signed(18 downto 0)')
        sub(f'signal reverbPartB_{side}          : signed(24 downto 0)',
            f'signal reverbPartB_{side}          : signed(19 downto 0)')
    for side, word in [('L', 'Left'), ('R', 'Right')]:
        spacing = '  ' if side == 'L' else ' '
        sub(f'reverbDown{word}{spacing}<= clamp16(shift_right('
            f'reverbPartA_{side} + reverbPartB_{side}, 4));',
            f'reverbDown{word} <= resize(shift_right('
            f'resize(reverbPartA_{side}, 20) + reverbPartB_{side}, 4), 16);')
        a = (f'reverbPartA_{side} <= resize(reverbInput{word}_v, 25)\n'
             f'                                         + shift_left('
             f'resize(reverb{side}_d1, 25), 2);')
        sub(a, a.replace(', 25)', ', 19)'))
        b = (f'reverbPartB_{side} <= shift_left(resize(reverb{side}_d2, 25), 2) '
             f'+ shift_left(resize(reverb{side}_d2, 25), 1)\n'
             f'                                         + shift_left('
             f'resize(reverb{side}_d3, 25), 2)\n'
             f'                                         + resize(reverb{side}_d4, 25);')
        sub(b, b.replace(', 25)', ', 20)'))
    return text


def spu_fir_width_text(text):
    require(hashlib.sha256(text.encode()).hexdigest() == EXPECTED,
            'Unreviewed SPU FIR source')
    result = _transform(text)
    require(hashlib.sha256(result.encode()).hexdigest() == OUTPUT,
            'FIR output differs from the measured implementation')
    return result


def prepare(directory, license_file=None):
    directory = Path(directory).resolve()
    require(directory.is_relative_to(ROOT / 'build') and not subprocess.run(
        ['git', 'check-ignore', '--quiet', str(directory / 'spu-fir-width/manifest.json')],
        cwd=ROOT).returncode, 'Ignored build project required')
    require(not any((directory / name).exists() for name in
                    ('db', 'incremental_db', 'output_files', 'spu-fir-width')),
            'Prepare FIR widths once, before synthesis')
    require(json.loads((directory / 'manifest.json').read_text()).get('reset_only'),
            'Reviewed FIR widths require reset-only configuration')
    qsf = directory / 'apf_top.qsf'
    text = qsf.read_text()
    assigned = assignments(directory, text)
    paths = {path for kind, path in assigned
             if kind == 'VHDL_FILE' and path.name == 'spu.vhd'}
    require(len(paths) == 1, 'Missing or duplicate SPU source')
    original = paths.pop()
    hold = directory / 'cpu-hold-margin/pocket_cpu_hold.sdc'
    hold_scope = hold.with_name('scope.tcl')
    require(('SDC_FILE', hold) in assigned and hold.is_file() and hold_scope.is_file(),
            'Reviewed FIR widths require the retained 150 ps CPU hold constraint')
    # Canonicalize only the copied project directory in the literal Tcl source.
    # The .15ns value, exact dependency and full uncertainty script stay pinned.
    canonical_hold = hold.read_text().replace(str(directory), '<PROJECT>')
    require(hashlib.sha256(canonical_hold.encode()).hexdigest() == HOLD and
            digest(hold_scope) in HOLD_SCOPE,
            'Reviewed FIR widths require the retained 150 ps CPU hold constraint')
    license_file = Path(license_file or UPSTREAM / 'LICENSE').resolve()
    require(license_file.is_file() and digest(license_file) == LICENSE,
            'Pinned MiSTer GPL-2.0 license required')
    protected = {str(path): digest(path) for path in (original, hold, hold_scope, license_file, qsf)}
    before = inputs(directory)
    result = spu_fir_width_text(original.read_text())
    work = directory / 'spu-fir-width'
    token = '"' + str(original) + '"'
    require(text.count(token) == 1, 'Expected one quoted SPU source assignment')
    replacement = text.replace(token, '"' + str(work / 'spu.vhd') + '"', 1)
    require(all(digest(Path(path)) == value for path, value in protected.items()) and
            before == inputs(directory), 'Project changed during FIR preparation')
    work.mkdir()
    (work / 'spu.vhd').write_text(result)
    shutil.copyfile(license_file, work / 'COPYING.GPL2')
    qsf.write_text(replacement)
    report = dict(original_inputs=before, protected_inputs=protected,
                  input_spu_sha256=EXPECTED, output_spu_sha256=OUTPUT,
                  preparer_sha256=digest(Path(__file__)), extra_cpu_hold_ns=0.15,
                  coefficient_or_stage_changes=False, added_clocks_or_cycles=0,
                  clock_and_constraint_changes=False, production_selected=False,
                  fit_verified=False, timing_verified=False, hardware_verified=False,
                  reference='docs/pocket-spu-fir-width.md')
    (work / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--license-file', type=Path,
                        help='Override the default path to the pinned MiSTer LICENSE')
    args = parser.parse_args()
    print(json.dumps(prepare(args.project, args.license_file), indent=2))
