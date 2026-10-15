#!/usr/bin/env python3
"""Measure the alternative PS-FPGA CPU/cache/GTE and rendering GPU on Pocket's FPGA."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_resource_targets import COMPILE_PROFILES, apply_compile_profile, digest, write_project
from run_resource_fit import fit_target
from tool_paths import ROOT, find_tool

UPSTREAM = ROOT / 'references/PS-FPGA'
PIN = '83141cbdfe60b0a76b8e7d24ae734a3c80e4fe3b'


def prepare(directory, candidate):
    if candidate == 'cpu':
        folders = [UPSTREAM / 'rtl/cpu', UPSTREAM / 'rtl/gte/rtl']
        entity, clock, source = 'psf_cpu_top', 'clk_i', folders[0] / 'psf_cpu_top.v'
        limitations = ['Full CPU, 4KiB instruction cache, scratchpad, AXI data path and original GTE included',
                       'Not integrated with MiSTer devices or Pocket platform',
                       'Upstream 32-byte cache lines and cache-isolation TODO differ from PSX behavior',
                       'Upstream psf_gte fixes GTE fast mode to one; CPU instructions are not executed here']
    else:
        folders = [UPSTREAM / 'rtl/gpu/rtl']
        entity, clock, source = 'gpu', 'clk', folders[0] / 'gpu.sv'
        limitations = ['Rendering GPU; display scanout and external memory controller absent',
                       'Upstream GP1 reset does not abort active primitives; rounding differences documented',
                       'All DIP controls exposed; not a matched fixed-control comparison with MiSTer',
                       'Not integrated with MiSTer/Pocket memory or CPU']
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', source.read_text(), flags=re.S)
    header = text[text.index('module ' + entity):].split(');', 1)[0]
    ports = re.findall(r'\b(input|output)\s+(?:reg\s+|wire\s+)?(\[\s*\d+\s*:\s*\d+\s*\]\s+)?(\w+)', header)
    if not ports:
        raise ValueError('Pinned candidate ports changed')
    external = []
    for direction, width, name in ports:
        # These ports exist only under the upstream LAXER debug define.
        if name.startswith('dbg_') or name == 'o_adrPrefetch':
            continue
        match = re.search(r'(\d+)\s*:\s*(\d+)', width)
        kind = f'std_logic_vector({match[1]} downto {match[2]})' if match else 'std_logic'
        external.append((name, 'in' if direction == 'input' else 'out', kind))
    sources = []
    for folder in folders:
        for file in sorted(folder.iterdir()):
            if file.suffix not in ('.sv', '.v', '.hv', '.inl'):
                continue
            sources.append({'path': str(file), 'library': 'work', 'sha256': digest(file),
                            'assignment': 'SYSTEMVERILOG_FILE' if file.suffix in ('.v', '.sv') else 'MISC_FILE'})
    directory.mkdir(parents=True)
    if candidate == 'cpu':
        original = UPSTREAM / 'rtl/cpu/psf_gte.v'
        text = original.read_text()
        if text.count('.i_regID(cop2_addr_w)') != 1:
            raise ValueError('Pinned CPU/GTE register association changed')
        # Typedefs must be outside the module: declaring them inside it sets
        # the upstream include guard without exposing the types to other files.
        text = '`include "GTEDefine.hv"\n' + text.replace('.i_regID(cop2_addr_w)', ".i_regID(E_REG'(cop2_addr_w))")
        adapted = directory / 'psf_gte.v'
        adapted.write_text(text)
        for entry in sources:
            if entry['path'] == str(original):
                entry.update(path=str(adapted), sha256=digest(adapted), original=str(original),
                             original_sha256=digest(original), transforms=['Explicit six-bit enum cast accepted by Quartus'])
    project = write_project(directory, entity, external, (clock,), sources,
                            ['# Internal clock only; no board constraints.',
                             f'create_clock -name core_clk -period 29.5251 [get_ports {clock}]',
                             'derive_clock_uncertainty'])
    qsf = directory / (entity + '.qsf')
    with qsf.open('a') as file:
        for folder in folders:
            file.write(f'set_global_assignment -name SEARCH_PATH "{folder}"\n')
    project['.qsf'] = digest(qsf)
    return {'top': entity, 'directory': str(directory), 'quartus_sources': sources, 'project_sha256': project,
            'fixed_ports': {'GTE fast mode (upstream)': 1} if candidate == 'cpu' else {},
            'physical_clock_ports': [clock], 'limitations': limitations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--targets', nargs='+', choices=('cpu', 'gpu'), default=['cpu', 'gpu'])
    parser.add_argument('--profile', choices=COMPILE_PROFILES, default='area-share')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/resources/psf-candidates')
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    if args.timeout <= 0 or len(args.targets) != len(set(args.targets)):
        parser.error('Positive timeout and unique targets required')
    quartus = find_tool('quartus_sh')
    if not quartus:
        parser.error('Quartus with Cyclone V support is required')
    references = [UPSTREAM]
    if 'cpu' in args.targets:
        references.append(UPSTREAM / 'rtl/gte')
    if 'gpu' in args.targets:
        references.append(UPSTREAM / 'rtl/gpu')
    pins = {}
    for reference in references:
        pin = subprocess.check_output(['git', '-C', str(reference), 'rev-parse', 'HEAD'], text=True).strip()
        expected = PIN if reference == UPSTREAM else subprocess.check_output(
            ['git', '-C', str(UPSTREAM), 'rev-parse', 'HEAD:' + reference.relative_to(UPSTREAM).as_posix()], text=True).strip()
        if pin != expected or subprocess.run(['git', '-C', str(reference), 'diff', '--quiet', 'HEAD']).returncode:
            parser.error('Reference differs from the recorded pin or has tracked changes')
        pins[str(reference)] = pin
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    prefix = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    run = Path(tempfile.mkdtemp(prefix=prefix, dir=output))
    manifest = {'device': '5CEBA4F23C8', 'reference_pins': pins, 'full_core': False,
                'hardware_deployable': False, 'targets': {name: prepare(run / name, name) for name in args.targets}}
    apply_compile_profile(manifest, args.profile)
    (run / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    results = {}
    for name, target in manifest['targets'].items():
        result = fit_target(Path(quartus).parent, target, manifest['device'], args.timeout)
        result['original_source_identity_verified'] = all('original' not in source or
            digest(Path(source['original'])) == source['original_sha256'] for source in target['quartus_sources'])
        results[name] = result
        print(name, 'fit=', result['fit_verified'], {k: v['used'] for k, v in result['counts'].items()}, flush=True)
    (run / 'report.json').write_text(json.dumps({'manifest': manifest, 'results': results}, indent=2) + '\n')
    print(run / 'report.json')
    if any(not result['fit_verified'] or not result['original_source_identity_verified'] for result in results.values()):
        raise SystemExit('Candidate measurement failed; inspect the recorded errors')


if __name__ == '__main__':
    main()
