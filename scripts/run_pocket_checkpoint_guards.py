#!/usr/bin/env python3
"""Reject stale candidate conversion/lid identities before model execution."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from prepare_resource_targets import digest
from tool_paths import ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True)
    parser.add_argument('--legacy-build',type=Path)
    args=parser.parse_args()
    build=args.build_dir.resolve();manifest_path=build/'build-manifest.json'
    original=json.loads(manifest_path.read_text())
    if original['preparation'].get('reset_header_constant_alias')!=1:
        parser.error('Use a fresh corrected candidate build for the guard test')
    dependencies=[ROOT/'scripts'/name for name in
        ('prepare_system_verilator.py','ghdl_verilog.py','run_system_checkpoint.py')]
    if not all(str(p) in original['protected_sha256'] for p in dependencies):
        raise ValueError('Candidate build omits shared conversion/protocol identity')
    parent=ROOT/'build/sim/pocket-checkpoint-guards'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    parent.mkdir(parents=True)
    protected={str(manifest_path):digest(manifest_path),str(Path(__file__)):digest(Path(__file__))}
    protected.update({str(p):digest(p) for p in dependencies})
    for path in (build/'system.v',build/'obj/Vpsx_checkpoint'):
        protected[str(path)]=digest(path)
    cases=[]
    variants=[('legacy-alias',None),('opposite-lid',None)]+[('changed-'+p.stem,p) for p in dependencies]
    if args.legacy_build:
        variants.append(('original-legacy-build',args.legacy_build.resolve()))
    for name,path in variants:
        folder=parent/name;folder.mkdir()
        if name=='original-legacy-build':
            selected=path;modified=json.loads((selected/'build-manifest.json').read_text())
            if modified['preparation'].get('reset_header_constant_alias')==1:
                raise ValueError('Selected legacy build is already corrected')
            for p in (selected/'build-manifest.json',selected/'system.v',selected/'obj/Vpsx_checkpoint'):
                protected[str(p)]=digest(p)
        else:
            selected=folder/'build';selected.mkdir()
            modified=json.loads(json.dumps(original))
            if name=='legacy-alias':modified['preparation'].pop('reset_header_constant_alias')
            elif name=='opposite-lid':modified['no_disc_lid_closed']=not original.get('no_disc_lid_closed',False)
            else:modified['protected_sha256'][str(path)]='0'*64
            (selected/'build-manifest.json').write_text(json.dumps(modified,indent=2)+'\n')
        output=folder/'run'
        command=['python3',str(ROOT/'scripts/run_pocket_checkpoint.py'),'--reuse','--build-dir',str(selected),
            '--output',str(output),'--max-cycles','1']
        if not modified['fast_reset']:command.append('--full-reset')
        if modified['fast_boot']:command.append('--fastboot')
        # Deliberately request the original lid, not the modified opposite one.
        if (modified if name=='original-legacy-build' else original).get('no_disc_lid_closed',False):
            command.append('--no-disc-lid-closed')
        expected=('Legacy candidate reset-header alias' if name in ('legacy-alias','original-legacy-build') else
            'Candidate no-disc lid selection differs' if name=='opposite-lid' else 'Candidate build changed')
        result=subprocess.run(command,capture_output=True,text=True,timeout=60)
        text=result.stdout+result.stderr;(folder/'rejection.log').write_text(text)
        passed=result.returncode!=0 and expected in text and not (output/'run.log').exists()
        cases.append({'name':name,'passed':passed,'exit_code':result.returncode,'expected':expected,
            'rejection_log_sha256':digest(folder/'rejection.log'),'model_executed':(output/'run.log').exists()})
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    report={'passed':identity and all(c['passed'] for c in cases),'source_identity_verified':identity,
        'protected_sha256':protected,'cases':cases,'game_verified':False,'hardware_verified':False}
    (parent/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(parent/'report.json');print(json.dumps(cases,indent=2))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
