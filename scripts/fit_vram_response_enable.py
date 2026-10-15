#!/usr/bin/env python3
"""Measure a cycle-preserving VRAM-enable change in a fresh physical top.

Uses the same area choices as d92b465. It retains the budget top's known CDC,
board timing and topology limitations; successful placement is not closure.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import re

from prepare_full_pocket import prepare_full, VARIANTS
from prepare_resource_targets import digest
from prepare_vram_response_enable import apply_response_enable
from tool_paths import ROOT, find_tool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/pocket/vram-response-enable')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--map-only', action='store_true',
                        help='Verify eight local enables before any full routing attempt')
    args = parser.parse_args()
    runs = args.output.resolve() / 'runs'
    runs.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(
        prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=runs))
    variants = list(VARIANTS) + [
        'dma-channel-ram', 'spu-reverb-table', 'gte-control-ram', 'gte-matrix-ram',
        'gpu-poly-share', 'gpu-vertex-ram', 'gpu-tag-generation', 'cd-ffvii']
    manifest = prepare_full(work, variants, reset_only=True, profile='area-min-state')
    manifest = apply_response_enable(manifest, work)
    manifest['limitations'].append(
        'Local VRAM response enable is timing work only; no host CDC or board exception changes')
    manifest['vram_response_enable']['preparation_sha256'] = digest(
        ROOT / 'scripts/prepare_vram_response_enable.py')
    (work / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(work, flush=True)
    if args.prepare_only:
        return
    binary = Path(find_tool('quartus_sh')).parent
    report = {'manifest': manifest, 'steps': [], 'fit_verified': False,
              'timing_verified': False, 'hardware_verified': False,
              'tool_version': subprocess.check_output([str(binary / 'quartus_sh'), '--version'], text=True)}
    with (work / 'quartus.log').open('w') as log:
        for name in (('quartus_map',) if args.map_only else ('quartus_map', 'quartus_fit', 'quartus_sta')):
            result = subprocess.run([str(binary / name), 'apf_top'], cwd=work,
                                    stdout=log, stderr=subprocess.STDOUT)
            report['steps'].append({'tool': name, 'exit_code': result.returncode})
            report['source_identity_verified'] = all(
                digest(Path(s['path'])) == s['sha256'] for s in manifest['sources'])
            report['project_identity_verified'] = all(
                digest(work / n) == sha for n, sha in manifest['project_hashes'].items())
            for kind in ('map', 'fit', 'sta'):
                path = work / f'output_files/apf_top.{kind}.summary'
                if path.exists():
                    report[kind + '_summary'] = {
                        'path': str(path), 'sha256': digest(path), 'text': path.read_text()}
            report['fit_verified'] = any(s['tool'] == 'quartus_fit' and s['exit_code'] == 0
                                        for s in report['steps']) and report['source_identity_verified'] and report['project_identity_verified']
            (work / 'quartus-report.json').write_text(json.dumps(report, indent=2) + '\n')
            print(name, result.returncode, flush=True)
            if result.returncode:
                raise SystemExit(result.returncode)
            if name == 'quartus_map':
                audit = ROOT / 'scripts/report_vram_response_enable.tcl'
                before = digest(work / 'apf_top.qsf')
                check = subprocess.run([str(binary / 'quartus_sta'), '-t', str(audit),
                    str(work / 'map-enable-check'), 'post-map'], cwd=work,
                    capture_output=True, text=True, timeout=120)
                output = check.stdout + check.stderr
                (work / 'map-enable-check.log').write_text(output)
                found = re.findall(r'VRAM_LOCAL_ENABLE_REGISTERS (\d+)', output)
                passed = check.returncode == 0 and found == ['8'] and digest(work / 'apf_top.qsf') == before
                report['post_map_enable_gate'] = {'passed':passed,'exit_code':check.returncode,
                    'registers':int(found[0]) if len(found)==1 else None,
                    'log_sha256':digest(work / 'map-enable-check.log'),'audit_sha256':digest(audit)}
                (work / 'quartus-report.json').write_text(json.dumps(report, indent=2) + '\n')
                print('Post-map eight-enable gate',passed,flush=True)
                if not passed:
                    raise SystemExit('Local enable mapping failed; do not run fitter')


if __name__ == '__main__':
    main()
