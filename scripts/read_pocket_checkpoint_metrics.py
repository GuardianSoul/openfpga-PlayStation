#!/usr/bin/env python3
"""Inspect finite-memory counters from a verified candidate checkpoint.

Compile a separate reader against its saved model library. Do not rebuild,
evaluate, advance or alter the running simulation or its checkpoint.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess

from prepare_pocket_system_sim import CONFIGURATION
from prepare_resource_targets import digest
from tool_paths import ROOT


def inspect(build, state, output):
    build, state, output = Path(build).resolve(), Path(state).resolve(), Path(output).resolve()
    if output.exists(): raise ValueError('Preserve existing metrics')
    manifest_path = build/'build-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    sidecar_path = state.with_suffix('.json')
    sidecar = json.loads(sidecar_path.read_text())
    if (manifest.get('configuration') != CONFIGURATION or
            sidecar['checkpoint_sha256'] != digest(state) or
            sidecar['fixed_inputs']['build_manifest_sha256'] != digest(manifest_path) or
            sidecar['fixed_inputs']['executable_sha256'] != digest(build/'obj/Vpsx_checkpoint')):
        raise ValueError('Candidate checkpoint/build identity differs')
    protected = dict(manifest['protected_sha256'])
    if not all(digest(Path(path)) == sha for path,sha in protected.items()):
        raise ValueError('Protected candidate source changed')
    obj = build/'obj'
    runtime_match = re.search(r'^VERILATOR_ROOT\s*=\s*(.+)$',
        (obj/'Vpsx_checkpoint.mk').read_text(), re.M)
    if not runtime_match: raise ValueError('Recorded Verilator runtime missing')
    runtime = Path(runtime_match[1].strip())
    work = build/'checkpoint-metrics'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    work.mkdir(parents=True)
    source = ROOT/'sim/reference/psx_checkpoint_metrics.cpp'
    header = ROOT/'sim/reference/psx_checkpoint_host.h'
    library = obj/'Vpsx_checkpoint__ALL.a'
    objects = [obj/(name+'.o') for name in ('verilated','verilated_save','verilated_threads')]
    reader = work/'read-metrics'
    inputs = [source, header, library, *objects, *obj.glob('*.h'),
        *runtime.glob('include/*.h'), manifest_path, state, sidecar_path]
    protected.update({str(path):digest(path) for path in inputs})
    compiler = shutil.which('g++')
    if not compiler: raise ValueError('C++ compiler required')
    with (work/'build.log').open('w') as log:
        subprocess.run([compiler, '-std=c++17', '-O2', '-I'+str(obj),
            '-I'+str(runtime/'include'), '-I'+str(header.parent), str(source),
            str(library), *map(str,objects), '-pthread','-latomic','-o',str(reader)],
            stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
    metrics = json.loads(subprocess.check_output([str(reader),str(state)],
        cwd=work, text=True, timeout=30))
    segment = sidecar['segment']
    for field in ('sectors','audio_frames','audio_nonzero'):
        if metrics[field] != segment[field]: raise ValueError('Saved '+field+' differs')
    if metrics['cycles'] != segment['end_cycle']:
        raise ValueError('Saved cycle differs')
    if not all(digest(Path(path)) == sha for path,sha in protected.items()):
        raise ValueError('Inspection inputs changed')
    result = {'metrics':metrics, 'read_only_no_model_evaluation':True,
        'source_and_checkpoint_identity_verified':True, 'reader_sha256':digest(reader),
        'protected_sha256':protected,
        'limitations':['Counters include reset and BIOS work; use checkpoint differences for game phases',
            'PSRAM payload counts reservations/accepted requests and can include an in-flight packet',
            'Occupied-model cycles include queue/functional overhead and are not physical bus utilization',
            'VRAM combines rendering/scanout/reset traffic; it does not split those clients',
            'Average rates cannot establish frame deadlines, burst behavior or board timing'],
        'hardware_verified':False}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(metrics,indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    inspect(args.build_dir,args.state,args.output)


if __name__ == '__main__': main()
