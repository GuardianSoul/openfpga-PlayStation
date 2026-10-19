#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Optional exact FIR arithmetic-to-DSP experiment; no default selection.

The selected lightweight coefficients, pipeline, history, reset/CE and output
schedule stay fixed. Physical fit/timing must be measured on each composition.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess

EXPECTED = 'b75586c8b309299bd16e5f558760d69aa21db738a15de35b2d9af5f153eda256'
OUTPUT = 'db90ac550d402d1711af185f9f3ffb709441f148e7bae36061779521644a043c'
WRAPPER = '25b05e370eb48c6ca0418ca85228fef6bd71f4fdb31bbed2d01d8ef42622e24a'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def spu_fir_dsp_text(text):
    require(hashlib.sha256(text.encode()).hexdigest() == EXPECTED,
            'Unreviewed SPU source; exact qualified FIR-width input required')
    t = text
    add = '   component psx_spu_fir_dsp is\n      port(x,h1,h2,h3,h4 : in std_logic_vector(15 downto 0);\n           parta : out std_logic_vector(18 downto 0);\n           partb : out std_logic_vector(19 downto 0));\n   end component;\n   signal fir_x_L, fir_x_R : signed(15 downto 0);\n   signal fir_a_L, fir_a_R : std_logic_vector(18 downto 0);\n   signal fir_b_L, fir_b_R : std_logic_vector(19 downto 0);\n'
    a = '\n   -- noise\n'
    assert t.count(a) == 1
    t = t.replace(a, add + '\n' + a)
    a = "                        if (cnt(0) = '1' and cnt(2) = '1') then\n                           reverbInputLeft_v  := clamp16(reverbsumleft  + resize(cd_next_left, 24));\n                           reverbInputRight_v := clamp16(reverbsumright + resize(cd_next_right, 24));\n                        else\n                           reverbInputLeft_v  := clamp16(reverbsumleft);\n                           reverbInputRight_v := clamp16(reverbsumright);\n                        end if;"
    assert t.count(a) == 1
    t = t.replace(a, '                        reverbInputLeft_v := fir_x_L;\n                        reverbInputRight_v := fir_x_R;')
    for side, word in [('L', 'Left'), ('R', 'Right')]:
        a = f'                        reverbPartA_{side} <= resize(reverbInput{word}_v, 19)\n                                         + shift_left(resize(reverb{side}_d1, 19), 2);\n                        reverbPartB_{side} <= shift_left(resize(reverb{side}_d2, 20), 2) + shift_left(resize(reverb{side}_d2, 20), 1)\n                                         + shift_left(resize(reverb{side}_d3, 20), 2)\n                                         + resize(reverb{side}_d4, 20);'
        assert t.count(a) == 1
        t = t.replace(a, f'                        reverbPartA_{side} <= signed(fir_a_{side});\n                        reverbPartB_{side} <= signed(fir_b_{side});')
    instances = ''
    for side, word in [('L', 'left'), ('R', 'right')]:
        instances += f"   fir_x_{side} <= clamp16(reverbsum{word} + resize(cd_next_{word},24))\n              when (cnt(0)='1' and cnt(2)='1') else clamp16(reverbsum{word});\n   fir_{word}: psx_spu_fir_dsp port map(\n      x=>std_logic_vector(fir_x_{side}), h1=>std_logic_vector(reverb{side}_d1),\n      h2=>std_logic_vector(reverb{side}_d2), h3=>std_logic_vector(reverb{side}_d3),\n      h4=>std_logic_vector(reverb{side}_d4), parta=>fir_a_{side}, partb=>fir_b_{side});\n"
    assert t.count('\nbegin\n') == 1
    t = t.replace('\nbegin\n', '\nbegin\n' + instances, 1)
    assert hashlib.sha256(t.encode()).hexdigest() == 'ef5333f1360809e86d8c117c5429fd8a7c8a0c5e63635e39d512066bf6171f67'
    assert 'when RAM_WAIT =>\n                  if (true) then' in t
    assert "elsif (SS_wren = '1') then" in t and "elsif (ce = '1') then" in t
    t, n = re.subn('(?m)^ +reverbPart[AB]_[LR] +<= [^;\\n]+;\\n', '', t)
    assert n == 8, n
    anchor = '      port(x,h1,h2,h3,h4 : in std_logic_vector(15 downto 0);'
    assert t.count(anchor) == 1
    t = t.replace(anchor, '      port(clk,enable,reset : in std_logic;\n           x,h1,h2,h3,h4 : in std_logic_vector(15 downto 0);')
    anchor = '   signal fir_x_L, fir_x_R : signed(15 downto 0);'
    assert t.count(anchor) == 1
    t = t.replace(anchor, anchor + '\n   signal fir_capture : std_logic;')
    block = "   fir_capture <= '1' when SS_wren='0' and ce='1' and state=RAM_WAIT and index=23 else '0';\n   reverbPartA_L <= signed(fir_a_L);\n   reverbPartB_L <= signed(fir_b_L);\n   reverbPartA_R <= signed(fir_a_R);\n   reverbPartB_R <= signed(fir_b_R);\n"
    assert t.count('\nbegin\n') == 1
    t = t.replace('\nbegin\n', '\nbegin\n' + block, 1)
    for side in ['left', 'right']:
        anchor = '   fir_' + side + ': psx_spu_fir_dsp port map(\n'
        assert t.count(anchor) == 1
        t = t.replace(anchor, anchor + '      clk=>clk1x, enable=>fir_capture, reset=>reset,\n')
    require(hashlib.sha256(t.encode()).hexdigest() == OUTPUT,
            'DSP output differs from the measured source')
    return t


def prepare(directory, wrapper=None):
    # Keep text transformation importable without initialized reference submodules.
    from measure_pocket_seeds import inputs
    from prepare_pocket_config_cdc import assignments
    from prepare_resource_targets import digest
    from prepare_spu_fir_width import HOLD, HOLD_SCOPE
    from tool_paths import ROOT

    directory = Path(directory).resolve()
    require(directory.is_relative_to(ROOT / 'build') and not subprocess.run(
        ['git', 'check-ignore', '--quiet', str(directory / 'fir-dsp/manifest.json')],
        cwd=ROOT).returncode, 'Ignored private build project required')
    require(not any((directory / name).exists() for name in
                    ['db', 'incremental_db', 'output_files', 'fir-dsp']),
            'Prepare DSP FIR once, before compilation')
    require(json.loads((directory / 'manifest.json').read_text()).get('reset_only'),
            'Reset-only composition required')
    qsf = directory / 'apf_top.qsf'
    qtext = qsf.read_text()
    assigned = assignments(directory, qtext)
    sources = {p for kind, p in assigned if kind == 'VHDL_FILE' and p.name == 'spu.vhd'}
    require(len(sources) == 1, 'One assigned SPU source required')
    original = sources.pop()
    hold = directory / 'cpu-hold-margin/pocket_cpu_hold.sdc'
    require(('SDC_FILE', hold) in assigned and hold.is_file(), 'Assigned 150 ps hold SDC required')
    require(hashlib.sha256(hold.read_text().replace(str(directory), '<PROJECT>').encode()).hexdigest() == HOLD
            and digest(hold.with_name('scope.tcl')) in HOLD_SCOPE,
            'Exact retained 150 ps hold constraint/scope required')
    wrapper = Path(wrapper or ROOT / 'rtl/pocket/psx_spu_fir_dsp.sv').resolve()
    require(wrapper.is_file() and digest(wrapper) == WRAPPER, 'Unreviewed DSP wrapper')
    before = inputs(directory)
    protected = {str(p): digest(p) for p in [original, hold, hold.with_name('scope.tcl'), qsf, wrapper]}
    output = spu_fir_dsp_text(original.read_text())
    token = '"' + str(original) + '"'
    require(qtext.count(token) == 1, 'One quoted absolute SPU assignment required')
    work = directory / 'fir-dsp'
    changed = qtext.replace(token, '"' + str(work / 'spu.vhd') + '"', 1)
    changed += '\nset_global_assignment -name SYSTEMVERILOG_FILE "' + str(work / 'psx_spu_fir_dsp.sv') + '" -library work\n'
    require(before == inputs(directory) and all(digest(Path(p)) == h for p, h in protected.items()),
            'Project changed during preparation')
    work.mkdir()
    (work / 'spu.vhd').write_text(output)
    shutil.copyfile(wrapper, work / 'psx_spu_fir_dsp.sv')
    qsf.write_text(changed)
    report = dict(original_inputs=before, protected_inputs=protected,
                  input_spu_sha256=EXPECTED, output_spu_sha256=OUTPUT,
                  wrapper_sha256=WRAPPER, preparer_sha256=digest(Path(__file__)),
                  extra_cpu_hold_ns=0.15, coefficients=[1,4,6,4,1],
                  added_pipeline_stages=0, added_cycles=0, selected=False,
                  existing_AB_stage_replaced=True, DSP_output_register_bits_requested=78,
                  reset_method="Synchronous zero coefficients and reset-forced clock enable",
                  clock_or_constraint_changes=False, expected_extra_DSP_blocks=6,
                  fit_verified=False, timing_verified=False, hardware_verified=False)
    (work / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--wrapper', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.project, args.wrapper), indent=2))
