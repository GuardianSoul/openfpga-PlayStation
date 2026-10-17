#!/usr/bin/env python3
"""Retained, source-bound same-clock GPU divider cohort; no default selection.

Only the reviewed reset-only GPU consumers are admitted. Inactive, idle and
initial post-launch partial divider outputs can differ. Active results and
completion retain the tested consumer schedule; this is not a generic divider.
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

SOURCES = {
    'gpu.vhd': '3698f4747ed3836832d84bb96fec57fc17e582a67c26899b9ec6338c80377a96',
    'gpu_poly.vhd': 'b9873962f71dfb78522a39079025800c5a982eb8c4f51bb5f6884ba59aa88796',
    'gpu_line.vhd': '0d43702adadceef2ce77ee01e7e222ba434622228e032cb8d71d3ee891d79254',
    'pGPU.vhd': '0ac89f49231daeef448d66f4458f76ad41ebf5c683a06695a4738b1b77b1f577',
    'divider.vhd': '13edea47cc1d5460bf4b155e7304c0806ac096c741a5cd493ed5f285e4f173b4',
}
TEMPLATE_SHA = 'd13b285ae3ae496272b176e5325fbab510adb7971aca8be8a9a5045acc45510e'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def gpu_text(text):
    require(hashlib.sha256(text.encode()).hexdigest() == SOURCES['gpu.vhd'],
            'Unreviewed GPU cohort consumer')
    marker = '   signal div_array                 : t_div_array;'
    require(text.count(marker) == 1, 'GPU divider declarations changed')
    text = text.replace(marker, marker + '''
   signal cohort_start:std_logic_vector(5 downto 0);
   signal cohort_n,cohort_q:std_logic_vector(269 downto 0);
   signal cohort_d,cohort_r:std_logic_vector(149 downto 0);
   signal cohort_done:std_logic;
''')
    marker = '   gdividers: for i in 0 to 5 generate'
    a = text.index(marker); b = text.index('   pixelColor  <=', a)
    block = text[a:b]
    old = '''      idivider : entity work.divider
      port map
      (
         clk       => clk2x,      
         start     => div_array(i).start,
         done      => div_array(i).done,          
         dividend  => div_array(i).dividend, 
         divisor   => div_array(i).divisor,  
         quotient  => div_array(i).quotient, 
         remainder => div_array(i).remainder
      );'''
    require(block.count(old) == 1, 'GPU divider instance changed')
    block = block.replace(old, '''      cohort_start(i)<=div_array(i).start;
      cohort_n(i*45+44 downto i*45)<=std_logic_vector(div_array(i).dividend);
      cohort_d(i*25+24 downto i*25)<=std_logic_vector(div_array(i).divisor);
      div_array(i).done<=cohort_done;
      div_array(i).quotient<=signed(cohort_q(i*45+44 downto i*45));
      div_array(i).remainder<=signed(cohort_r(i*25+24 downto i*25));''')
    block += '''   divider_cohort:entity work.psx_gpu_divider_cohort port map(
      clk=>clk2x,starts=>cohort_start,dividends=>cohort_n,divisors=>cohort_d,
      quotients=>cohort_q,remainders=>cohort_r,done=>cohort_done);

'''
    return text[:a] + block + text[b:]


def prepare(directory):
    directory = Path(directory).resolve()
    require(directory.is_relative_to(ROOT / 'build') and not subprocess.run(
        ['git', 'check-ignore', '--quiet', str(directory / 'divider-cohort/manifest.json')],
        cwd=ROOT).returncode, 'Ignored build project required')
    require(not any((directory / n).exists() for n in
                    ('db', 'incremental_db', 'output_files', 'divider-cohort')),
            'Prepare cohort once, before synthesis')
    require(json.loads((directory / 'manifest.json').read_text()).get('reset_only'),
            'Reviewed cohort requires reset-only configuration')
    hold = directory / 'cpu-hold-margin/pocket_cpu_hold.sdc'
    require(hold.is_file() and 'set pocket_cpu_hold_ns 0.15' in hold.read_text(),
            'Measured cohort requires the retained 150 ps CPU hold target')
    qsf = directory / 'apf_top.qsf'; before_qsf = digest(qsf); text = qsf.read_text()
    assigned = assignments(directory, text); selected = {}
    for name, sha in SOURCES.items():
        paths = {p for kind, p in assigned if kind == 'VHDL_FILE' and p.name == name}
        require(len(paths) == 1, 'Missing or duplicate cohort source: ' + name)
        path = paths.pop(); require(digest(path) == sha, 'Unreviewed cohort source: ' + name)
        selected[name] = path
    require(not any(p.name == 'psx_gpu_divider_cohort.vhd' for _, p in assigned),
            'Cohort already assigned')
    template = ROOT / 'rtl/pocket/psx_gpu_divider_cohort.vhd'
    require(digest(template) == TEMPLATE_SHA, 'Unreviewed cohort template')
    protected = {str(p): digest(p) for p in (*selected.values(), template)}
    original = inputs(directory); work = directory / 'divider-cohort'
    copies = {'gpu.vhd': gpu_text(selected['gpu.vhd'].read_text())}
    replacement = text
    for name in copies:
        token = '"' + str(selected[name]) + '"'
        require(replacement.count(token) == 1, 'Expected one quoted source pointer: ' + name)
        replacement = replacement.replace(token, '"' + str(work / name) + '"', 1)
    require(digest(qsf) == before_qsf and all(digest(Path(p)) == h for p, h in protected.items())
            and all(digest(Path(r['path'])) == r['sha256'] for r in original),
            'Project changed while preparing cohort')
    work.mkdir()
    for name, body in copies.items():
        (work / name).write_text(body)
    shutil.copyfile(template, work / template.name)
    shutil.copyfile(UPSTREAM / 'LICENSE', work / 'COPYING.GPL2')
    replacement += f'\nset_global_assignment -name VHDL_FILE "{work / template.name}" -library work\n'
    qsf.write_text(replacement)
    report = dict(original_inputs=original, sources=protected,
                  selected_sources={n: digest(work / n) for n in [*copies, template.name]},
                  preparer_sha256=digest(Path(__file__)), additional_gpu_clocks=0,
                  clock_and_constraint_changes=False, borrowed_divisors=False, shared_divisor_banks=4, parallel_lanes=6,
                  idle_and_initial_partial_values_unchanged=False,
                  full_port_equivalence=False, production_selected=False,
                  fit_verified=False, timing_verified=False, hardware_verified=False)
    (work / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    print(json.dumps(prepare(parser.parse_args().project), indent=2))
