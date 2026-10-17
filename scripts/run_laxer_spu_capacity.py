#!/usr/bin/env python3
"""Measure the unchanged pinned Laxer SPU; no functional/core selection."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

from prepare_laxer_spu_target import prepare
from prepare_resource_targets import COMPILE_PROFILES, apply_compile_profile, digest
from run_resource_fit import fit_target
from tool_paths import ROOT, find_tool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/resources/laxer-spu')
    parser.add_argument('--profile', choices=COMPILE_PROFILES, default='area-min-state')
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('Positive per-tool timeout required')
    quartus = find_tool('quartus_sh')
    if not quartus:
        parser.error('Quartus with Cyclone V support is required')
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=output))
    manifest = prepare(run / 'spu')
    apply_compile_profile(manifest, args.profile)
    target = manifest['targets']['laxer_spu']
    result = fit_target(Path(quartus).parent, target, manifest['device'], args.timeout)
    result['original_source_identity_verified'] = all(
        'original' not in s or digest(Path(s['original'])) == s['original_sha256']
        for s in target['quartus_sources'])
    result['measurement_valid'] = bool(result['fit_verified'] and result['source_identity_verified'] and
                                       result['project_identity_verified'] and result['original_source_identity_verified'] and
                                       len(result['steps']) == 3 and all(s['exit_code'] == 0 for s in result['steps']))
    result['functional_verified'] = False
    result['replacement_selected'] = False
    report = run / 'report.json'
    report.write_text(json.dumps(dict(manifest=manifest, result=result), indent=2) + '\n')
    print(json.dumps(dict(report=str(report), measurement_valid=result['measurement_valid'],
                          counts=result['counts'], errors=result['errors'])), flush=True)
    if not result['measurement_valid']:
        raise SystemExit('Component measurement failed; inspect recorded errors')


if __name__ == '__main__':
    main()
