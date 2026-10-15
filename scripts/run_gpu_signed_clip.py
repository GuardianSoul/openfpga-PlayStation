#!/usr/bin/env python3
"""Check negative-X polygon clipping and reject the pre-cfafa1d translation.

Fresh private preparations only; no cached system/checkpoint build is modified.
"""
import json
from pathlib import Path
import re
import subprocess
import tempfile

import prepare_system_verilator as conversion
from prepare_resource_targets import digest
import run_gpu_line_share as share
from tool_paths import ROOT, find_tool

FIXED = 'xStart(43 downto 32) > signed(resize(drawingAreaRight, 12))'
LEGACY = 'to_integer(xStart(43 downto 32)) > drawingAreaRight'


def bind_bench(directory, verilog, module):
    template = (ROOT / 'sim/reference/tb_gpu_primitives.sv').read_text()
    text = template[:template.index('initial begin\n repeat(100)')]
    text += (ROOT / 'sim/reference/tb_gpu_signed_clip_body.sv').read_text()
    source = verilog.read_text()
    header = source[source.index('module ' + module):].split(');', 1)[0]
    ports = {n.lower(): n for n in re.findall(
        r'\b(?:input|output)\s+(?:\[\d+:\d+\]\s+)?(\w+)', header)}
    instance = re.search(r'GPU_ENTITY gpu\(.*?\);', text, re.S).group()
    bound = re.sub(r'\.(\w+)\(', lambda m: '.' + ports[m[1].lower()] + '(', instance)
    text = text.replace(instance, bound).replace('GPU_ENTITY', module)
    path = directory / 'tb_gpu_signed_clip.sv'
    path.write_text(text)
    return path


def main():
    ghdl, verilator = find_tool('ghdl'), find_tool('verilator')
    if not ghdl or not verilator:
        raise SystemExit('GHDL synthesis and Verilator required')
    parent = ROOT / 'build/sim/gpu-signed-clip'
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent))
    models = ROOT / 'sim/reference/mister_memory_models.sv'
    inputs = [Path(__file__), ROOT / 'scripts/prepare_system_verilator.py',
              ROOT / 'scripts/run_gpu_line_share.py', models,
              ROOT / 'sim/reference/tb_gpu_primitives.sv',
              ROOT / 'sim/reference/tb_gpu_signed_clip_body.sv']
    protected = {str(p): digest(p) for p in inputs}
    original = conversion.gpu_transcode_source
    results = []
    for legacy in (False, True):
        case = work / ('legacy-fault' if legacy else 'corrected')
        case.mkdir()

        def transform(name, text, gpu_timing_prune=False):
            result = original(name, text, gpu_timing_prune)
            if legacy and name == 'gpu_poly':
                if result.count(FIXED) != 1:
                    raise ValueError('Signed polygon comparison changed')
                result = result.replace(FIXED, LEGACY)
            return result

        conversion.gpu_transcode_source = transform
        try:
            with (case / 'build.log').open('w') as log:
                manifest, verilog, module = share.transcode(case / 'sources', False, ghdl, log)
                bench = bind_bench(case, verilog, module)
                subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '4',
                    '-Wno-fatal', '--output-split', '20000', '--top-module', 'tb_gpu_probe',
                    '--Mdir', str(case / 'obj'), str(verilog), str(models), str(bench)],
                    cwd=case, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
        finally:
            conversion.gpu_transcode_source = original
        executable = case / 'obj/Vtb_gpu_probe'
        result = subprocess.run([str(executable)], cwd=case, capture_output=True, text=True, timeout=120)
        output = result.stdout + result.stderr
        (case / 'run.log').write_text(output)
        identity = all(digest(Path(s['path'])) == s['sha256'] and
            digest(Path(s['original'])) == s['original_sha256'] and
            ('selected' not in s or digest(Path(s['selected'])) == s['selected_sha256'])
            for s in manifest['sources'])
        accepted = identity and (bool(re.search(r'PASS GPU signed clip checks=23698\b', output))
            if not legacy else ('SIGNED_CLIP positive-control checks=6724' in output and
                               bool(re.search(r'GPU pixel \(\s*0,\s*40\) got=0000 expected=001f', output))))
        accepted = bool(accepted and ((result.returncode != 0) if legacy else (result.returncode == 0)))
        results.append(dict(legacy_comparison_fault=legacy, accepted=accepted,
            source_identity_verified=identity, preparation=manifest,
            protected_sha256={str(p): digest(p) for p in (verilog, bench, executable, case / 'run.log')},
            exit_code=result.returncode, stdout=output))
        print(output, end='', flush=True)
        if not accepted:
            raise SystemExit('Clipping fixture failed: ' + str(case))
    if any(digest(Path(p)) != sha for p, sha in protected.items()):
        raise SystemExit('Fixture inputs changed during execution')
    (work / 'report.json').write_text(json.dumps(dict(passed=True,
        protected_sha256=protected, results=results,
        ghdl_version=subprocess.check_output([ghdl, '--version'], text=True),
        verilator_version=subprocess.check_output([verilator, '--version'], text=True).strip(),
        pixel_checks=23698, coverage=['Positive-X control', 'Negative-X triangle clipped at x=0',
            'Negative span from signed drawing offset', 'Right-of-area rejection and no VRAM wrap'],
        game_verified=False, hardware_verified=False, cycle_equivalence_verified=False), indent=2) + '\n')
    print(work)


if __name__ == '__main__':
    main()
