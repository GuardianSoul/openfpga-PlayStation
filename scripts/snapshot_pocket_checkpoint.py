#!/usr/bin/env python3
"""Copy a closed candidate checkpoint while its worker continues running.

The original driver closes the checkpoint before emitting checkpoint-segment.
The normal Python runner emits its sidecar only when the worker exits. Bind a
private inspection copy to that closed marker, the worker's verified initial
checkpoint/fixed inputs and exact build; then deserialize read-only counters
to check the marker. Never edit the live checkpoint or claim portable state.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess

from prepare_pocket_system_sim import CONFIGURATION
from prepare_resource_targets import digest
from read_pocket_checkpoint_metrics import inspect
from run_checkpoint_observer import verify_hashes
from run_system_checkpoint import MAX_CYCLES
from tool_paths import ROOT


def snapshot(build, live_run, identity_state, output):
    build, live_run, identity_state, output = [Path(p).resolve()
        for p in (build,live_run,identity_state,output)]
    if output.exists() or not output.is_relative_to(ROOT) or subprocess.run(
        ['git','check-ignore','--quiet',str(output/'checkpoint.bin')],cwd=ROOT).returncode:
        raise ValueError('Use a fresh Git-ignored inspection directory')
    manifest_path=build/'build-manifest.json'
    manifest=json.loads(manifest_path.read_text())
    identity_sidecar=identity_state.with_suffix('.json')
    identity=json.loads(identity_sidecar.read_text())
    fixed=identity['fixed_inputs']
    if (manifest.get('configuration')!=CONFIGURATION or
        fixed['build_manifest_sha256']!=digest(manifest_path) or
        fixed['executable_sha256']!=digest(build/'obj/Vpsx_checkpoint') or
        identity['checkpoint_sha256']!=digest(identity_state)):
        raise ValueError('Candidate worker identity differs')
    state=live_run/'checkpoint.bin'
    marker_path=live_run/'checkpoint-segment.json'
    marker=json.loads(marker_path.read_text())
    if not identity['segment']['end_cycle']<=marker['end_cycle']<=MAX_CYCLES:
        raise ValueError('Closed marker predates the worker identity')
    protected=dict(manifest['protected_sha256'])
    for path in (manifest_path,identity_state,identity_sidecar,state,marker_path,Path(__file__)):
        protected[str(path)]=digest(path)
    stat_before=state.stat()
    for name,key in (('boot.hex8','boot_sha256'),('expected.hex','expected_sha256'),
                     ('trackinfo.hex32','disc_metadata_sha256')):
        if key in fixed:
            path=live_run/name
            if digest(path)!=fixed[key]:
                raise ValueError('Worker fixed input differs: '+name)
            protected[str(path)]=fixed[key]
    if 'disc_image_sha256' in fixed:
        disc_path=live_run/'disc-host.json'
        disc=json.loads(disc_path.read_text())
        image,cue=Path(disc['image']),Path(disc['cue'])
        if digest(image)!=fixed['disc_image_sha256'] or digest(cue)!=fixed['cue_sha256']:
            raise ValueError('Worker disc identity differs')
        protected.update({str(disc_path):digest(disc_path),str(image):fixed['disc_image_sha256'],
                          str(cue):fixed['cue_sha256']})
    verify_hashes(protected)
    output.mkdir(parents=True)
    for name in ('boot.hex8','expected.hex','trackinfo.hex32','disc-host.json','checkpoint.bin'):
        path=live_run/name
        if path.exists():
            shutil.copy2(path,output/name)
            if digest(output/name)!=protected[str(path)]:
                raise ValueError('Closed snapshot copy differs: '+name)
    stat_after=state.stat()
    if (stat_before.st_size,stat_before.st_mtime_ns)!=(stat_after.st_size,stat_after.st_mtime_ns):
        raise ValueError('Live checkpoint changed while copying')
    provenance={'kind':'closed worker checkpoint inspection copy',
        'live_checkpoint':str(state),'closed_marker':str(marker_path),
        'closed_marker_sha256':protected[str(marker_path)],
        'identity_checkpoint':str(identity_state),
        'identity_checkpoint_sha256':identity['checkpoint_sha256'],
        'same_model_and_fixed_inputs':True,'cross_model_transfer':False}
    copied=output/'checkpoint.bin'
    copied.with_suffix('.json').write_text(json.dumps({'fixed_inputs':fixed,
        'checkpoint_sha256':digest(copied),'segment':marker,'inspection_provenance':provenance},indent=2)+'\n')
    try:
        # This reader performs no eval: saved cycle/sector/PCM counts must
        # match the closed marker before the inspection copy is accepted.
        counters=inspect(build,copied,output/'metrics.json')
        verify_hashes(protected)
    except Exception:
        # Preserve rejected bytes/reports without leaving an accepted sidecar
        # that another runner could mistake for a verified inspection copy.
        copied.with_suffix('.json').rename(output/'rejected-sidecar.json')
        raise
    result={'passed':True,'provenance':provenance,'protected_sha256':protected,
        'copied_state_sha256':digest(copied),'read_only_saved_counts_match_marker':True,
        'metrics':counters['metrics'],'live_worker_completion_verified':False,
        'hardware_verified':False}
    (output/'snapshot-report.json').write_text(json.dumps(result,indent=2)+'\n')
    print(output)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True)
    parser.add_argument('--live-run',type=Path,required=True)
    parser.add_argument('--identity-state',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    output=args.output or ROOT/'build/sim/checkpoint-snapshot'/\
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    snapshot(args.build_dir,args.live_run,args.identity_state,output)


if __name__=='__main__':
    main()
