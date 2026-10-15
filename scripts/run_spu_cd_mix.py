#!/usr/bin/env python3
"""Check the pinned SPU's CD mixer or bounded authored voice playback."""
import json
import argparse
from pathlib import Path
import re
import subprocess

from prepare_resource_targets import digest
from prepare_system_verilator import prepare
from tool_paths import ROOT, find_tool
from ghdl_verilog import normalize_generated_verilog


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--voices',action='store_true',help='Check authored ADPCM voices, envelopes and 24-voice accumulation')
    args=parser.parse_args()
    ghdl = find_tool('ghdl')
    verilator = find_tool('verilator') or str(ROOT / 'build/tools/oss-cad-suite/bin/verilator')
    if not ghdl or not Path(verilator).is_file():
        raise SystemExit('GHDL synthesis and Verilator are required')
    directory = ROOT / ('build/sim/spu-voices' if args.voices else 'build/sim/spu-cd-mix')
    directory.mkdir(parents=True, exist_ok=True)
    manifest = prepare(directory / 'sources')
    verilog = directory / 'spu.v'
    template = ROOT / ('sim/reference/tb_spu_voice.sv' if args.voices else 'sim/reference/tb_spu_cd_mix.sv')
    bench = directory / 'tb_spu_cd_mix.sv'
    models = ROOT / 'sim/reference/mister_memory_models.sv'
    executable = directory / 'obj/Vtb_spu_mix'
    with (directory / 'build.log').open('w') as log:
        for source in manifest['sources']:
            subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', f'--work={source["library"]}', source['path']],
                           cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=60, check=True)
        with verilog.open('w') as output:
            subprocess.run([ghdl, '--synth', '--std=08', '-frelaxed', '--vendor-library=ramip',
                            '--out=verilog', 'spu'], cwd=directory, stdout=output, stderr=log, timeout=120, check=True)
        normalization = normalize_generated_verilog(verilog)
        converted = verilog.read_text()
        matches = re.findall(r'^module (spu(?:_Barch[^\s]*)?)[ \t]*\n', converted, re.M)
        if len(matches) != 1:
            raise SystemExit('Expected exactly one SPU entity')
        header = converted[re.search(r'^module ' + re.escape(matches[0]) + r'[ \t]*\n', converted, re.M).start():].split(');', 1)[0]
        ports = {name.lower(): name for name in re.findall(r'\b(?:input|output)\s+(?:\[\d+:\d+\]\s+)?(\w+)', header)}
        text = template.read_text()
        instance = re.search(r'SPU_ENTITY spu_core\([^\n]+\);', text).group()
        bound = re.sub(r'\.(\w+)\(', lambda m: '.' + ports[m[1].lower()] + '(', instance)
        bench.write_text(text.replace(instance, bound).replace('SPU_ENTITY', matches[0]))
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '4', '-Wno-fatal',
                        '--top-module', 'tb_spu_mix', '--Mdir', str(directory / 'obj'),
                        str(verilog), str(models), str(bench)], cwd=directory,
                       stdout=log, stderr=subprocess.STDOUT, timeout=300, check=True)
    protected = {str(path): digest(path) for path in (verilog, template, bench, models, executable)}
    result = subprocess.run([str(executable)], cwd=directory, capture_output=True, text=True, timeout=60)
    output = result.stdout + result.stderr
    (directory / 'run.log').write_text(output)
    print(output, end='', flush=True)
    identity = all(digest(Path(path)) == sha for path, sha in protected.items())
    identity &= all(digest(Path(s['path'])) == s['sha256'] and
                    digest(Path(s['original'])) == s['original_sha256'] and
                    ('selected' not in s or digest(Path(s['selected'])) == s['selected_sha256'])
                    for s in manifest['sources'])
    if args.voices:
        matched=re.search(r'PASS SPU voice cases=\s*51 pcm_checks=\s*3928 envelope_checks=\s*1836 loop_checks=\s*51 max_voices=24 accumulation_checks=256 attack_steps=\s*153',output)
        passed=result.returncode==0 and identity and bool(matched)
    else:
        matched = re.search(r'PASS SPU CD mixer checks=\s*384 ticks=\s*(\d+) period_checks=\s*(\d+)', output)
        passed = result.returncode == 0 and identity and bool(matched) and int(matched[2]) >= 1535
    report = {'preparation': manifest, 'verilog_normalization': normalization,
              'protected_sha256': protected, 'source_and_input_identity_verified': identity,
              'ghdl_version': subprocess.check_output([ghdl, '--version'], text=True),
              'verilator_version': subprocess.check_output([verilator, '--version'], text=True).strip(),
              'passed': passed, 'exit_code': result.returncode, 'stdout': output, 'stereo_sample_checks': 3928 if args.voices else 384,
              'tick_period_cycles': 768, 'tick_period_checks': int(matched[2]) if matched and not args.voices else None,
              'coverage': ['Signed stereo CD inputs', 'Positive and negative CD/main volume',
                           'Independent channel gains', 'Zero volume', 'CD input enable/disable'],
              'adpcm_verified': False, 'adsr_verified': False, 'gaussian_interpolation_verified': False,
              'reverb_verified': False,
              'xa_decoder_verified': False, 'game_audio_verified': False, 'hardware_verified': False}
    if args.voices:
        report.update(cases=51,envelope_checks=1836,loop_end_checks=51,maximum_voices=24,
            accumulation_checks=256,attack_steps=153,coverage=['Filter-zero signed ADPCM','Shifts 0/5/12 and reserved 13/14/15',
                'Loop-start/end/repeat flags','Stable full envelope and key-off mute',
                'Signed channel gains','24 simultaneous voices and no observed sample overrun'],
            bounded_adpcm_scope='Filter-zero cases only',
            bounded_adsr_scope='Fast attack, stable level and key-off cases only',
            bounded_interpolation_scope='Constant samples at zero fractional pitch only',
            voice_ram_input='Authored direct model preload; CPU/DMA sample upload not checked')
    (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if not passed:
        raise SystemExit(f'SPU checks failed; see {directory}/run.log')


if __name__ == '__main__':
    main()
