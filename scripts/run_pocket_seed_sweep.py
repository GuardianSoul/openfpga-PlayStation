#!/usr/bin/env python3
"""Fit three seeds from one mapped connected budget snapshot; audit actual paths.

The native main/SPU/CD topology is not selected by this legacy-budget sweep.
No timing exceptions, feature changes or target relaxation are introduced.
"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from prepare_resource_targets import digest
from run_resource_fit import validated_counts,timing_metrics
from tool_paths import ROOT,find_tool


def audit_paths(directory):
    rows=[]
    for path in sorted(directory.glob('corner*-cpu*txt')):
        text=path.read_text(errors='replace')
        for block in text.split('Path #')[1:]:
            match=re.match(r'(\d+): (Setup|Hold) slack is (-?\d+(?:\.\d+)?)',block)
            if not match:raise ValueError('Incomplete CPU timing path: '+str(path))
            row=dict(report=path.name,path=int(match[1]),kind=match[2].lower(),slack_ns=float(match[3]))
            for name in ['From Node','To Node','Launch Clock','Latch Clock']:
                field=re.search(r'^;\s*'+re.escape(name)+r'\s*;\s*([^;]+?)\s*;',block,re.M)
                if not field:raise ValueError('Missing timing field '+name)
                row[name]=field[1].strip()
            rows.append(row)
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume',type=Path,help='Resume a source-verified mapped snapshot without repeating completed fits')
    parser.add_argument('--cpu-tag-contract',type=Path)
    parser.add_argument('--release-boot-contract',type=Path)
    parser.add_argument('--apf-boot-contract',type=Path)
    args=parser.parse_args()
    script=Path(__file__);audit_script=ROOT/'scripts/report_pocket_integrated.tcl'
    protected={str(p):digest(p) for p in [script,audit_script,ROOT/'scripts/pocket_audio_scope.tcl']}
    if args.resume:
        sweep=args.resume.resolve();report=json.loads((sweep/'report.json').read_text())
        base=Path(report['mapped_snapshot']);manifest=json.loads((base/'manifest.json').read_text())
        if digest(base/'manifest.json')!=report['manifest_sha256']:raise ValueError('Mapped snapshot manifest changed')
        if [{k:s[k] for k in ['path','sha256']} for s in manifest['sources']]!=report['source_snapshot']:
            raise ValueError('Mapped source set changed')
        for source in report['source_snapshot']:protected[source['path']]=source['sha256']
        coefficients=manifest['pocket_audio_stream']['coefficients']
        protected[coefficients['compact_path']]=coefficients['compact_sha256']
        if any(digest(Path(p))!=sha for p,sha in protected.items()):raise ValueError('Resume source identity failed')
        report.setdefault('runner_resumes',[]).append({'runner_sha256':digest(script),
            'reason':'Completed fit preserved; report decoding tolerates ANSI degree characters; no hardware/project change'})
    else:
        if not all([args.cpu_tag_contract,args.release_boot_contract,args.apf_boot_contract]):
            parser.error('New sweep requires CPU tag, release boot and APF contracts')
        parent=ROOT/'build/pocket/seed-sweeps';parent.mkdir(parents=True,exist_ok=True)
        sweep=Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),dir=parent))
        script=Path(__file__);audit_script=ROOT/'scripts/report_pocket_integrated.tcl'
        protected={str(p):digest(p) for p in [script,audit_script,ROOT/'scripts/pocket_audio_scope.tcl']}
        command=[sys.executable,str(ROOT/'scripts/fit_pocket_audio_top.py'),'--map-only','--preset','current',
            '--cpu-tag-contract',str(args.cpu_tag_contract.resolve()),'--vram-response-enable',
            '--release-boot-contract',str(args.release_boot_contract.resolve()),'--apf-boot-contract',str(args.apf_boot_contract.resolve())]
        print('Seed sweep '+str(sweep),flush=True)
        result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=600)
        (sweep/'prepare.log').write_text(result.stdout+result.stderr)
        if result.returncode:raise SystemExit('Mapped snapshot failed: '+str(sweep/'prepare.log'))
        candidates=[Path(row.strip()) for row in result.stdout.splitlines() if row.startswith(str(ROOT/'build/pocket/audio-stream/runs')+'/')]
        if len(candidates)!=1:raise ValueError('Ambiguous mapped snapshot')
        base=candidates[0];manifest=json.loads((base/'manifest.json').read_text())
        mapped=json.loads((base/'quartus-report.json').read_text())
        if not mapped['post_map_enable_gate']['passed'] or not mapped['mapped_rom']['passed']:
            raise ValueError('Mapped snapshot contract failed')
        if not all(mapped[k] for k in ['source_identity_verified','project_identity_verified','coefficient_identity_verified']):
            raise ValueError('Mapped snapshot identity failed')
        for source in manifest['sources']:protected[source['path']]=source['sha256']
        coefficients=manifest['pocket_audio_stream']['coefficients']
        protected[coefficients['compact_path']]=coefficients['compact_sha256']
        source_snapshot=[{k:s[k] for k in ['path','sha256']} for s in manifest['sources']]
        report=dict(mapped_snapshot=str(base),mapped_report_sha256=digest(base/'quartus-report.json'),
            manifest_sha256=digest(base/'manifest.json'),selected_preset=manifest['audio_fit_preset'],
            compile_profile=manifest['compile_profile'],compile_settings=manifest['compile_settings'],
            source_snapshot=source_snapshot,seeds=[],target_LABs=1663,hardware_verified=False,
            native_topology_verified=False,new_timing_exceptions=False)
        # Clone all mapped databases BEFORE any seed fits: no later seed reuses
        # placement/routing or incremental feedback from an earlier one.
        for seed in [1,2,3]:
            work=sweep/f'seed-{seed}';shutil.copytree(base,work)
            qsf=work/'apf_top.qsf';text=qsf.read_text()
            if text.count('set_global_assignment -name SEED 1\n')!=1:raise ValueError('Expected one seed assignment')
            qsf.write_text(text.replace('set_global_assignment -name SEED 1\n',f'set_global_assignment -name SEED {seed}\n'))
    def save(): (sweep/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    save()
    binary=Path(find_tool('quartus_sh')).parent
    for seed in [1,2,3]:
        work=sweep/f'seed-{seed}';projects={n:digest(work/n) for n in manifest['project_hashes']}
        row=next((r for r in report['seeds'] if r['seed']==seed),None)
        if row is not None and row['project_hashes']!=projects:raise ValueError('Resume seed project changed')
        if row is None:
            row=dict(seed=seed,directory=str(work),steps=[],project_hashes=projects,fit_verified=False)
            report['seeds'].append(row)
        save()
        if not row['steps']:
            print(f'SEED {seed} fitter starting {work}',flush=True)
            with (work/'seed-quartus.log').open('w') as log:
                for tool in ['quartus_fit','quartus_sta']:
                    r=subprocess.run([str(binary/tool),'apf_top'],cwd=work,stdout=log,stderr=subprocess.STDOUT,timeout=3600)
                    row['steps'].append(dict(tool=tool,exit_code=r.returncode));save()
                    if r.returncode:break
        elif row['steps'][-1]['tool']=='quartus_fit' and row['steps'][-1]['exit_code']==0:
            with (work/'seed-quartus.log').open('a') as log:
                r=subprocess.run([str(binary/'quartus_sta'),'apf_top'],cwd=work,stdout=log,stderr=subprocess.STDOUT,timeout=3600)
            row['steps'].append(dict(tool='quartus_sta',exit_code=r.returncode));save()
        identity=all(digest(Path(p))==sha for p,sha in protected.items()) and all(digest(work/n)==sha for n,sha in projects.items())
        row['source_and_project_identity_verified']=identity
        if not identity:save();raise ValueError('Sweep source/project changed')
        summary=work/'output_files/apf_top.fit.summary'
        row['fit_summary_sha256']=digest(summary) if summary.exists() else None
        if row['steps'][0]['exit_code']==0:
            row['counts']=validated_counts(summary.read_text(),'apf_top','5CEBA4F23C8');row['fit_verified']=True
            text=(work/'output_files/apf_top.fit.rpt').read_text(errors='replace')
            match=re.search(r'^;\s*Total LABs:\s*partially or completely used\s*;\s*(\d+) / 1848',text,re.M)
            if not match:raise ValueError('Missing true LAB allocation')
            row['LABs']=int(match[1]);row['deficit_to_1663']=row['LABs']-1663
            if row['steps'][-1]['exit_code']==0:
                row['reported_timing']=timing_metrics((work/'output_files/apf_top.sta.summary').read_text())
                output=work/'seed-audit'
                r=subprocess.run([str(binary/'quartus_sta'),'-t',str(audit_script),str(output)],cwd=work,capture_output=True,text=True,timeout=300)
                (work/'seed-audit.log').write_text(r.stdout+r.stderr)
                row['audit_exit_code']=r.returncode
                if r.returncode:save();raise ValueError('Read-only seed audit failed')
                if any(digest(work/n)!=sha for n,sha in projects.items()):raise ValueError('Audit exported assignments')
                row['CPU_paths']=audit_paths(output)
                row['audit_reports']={p.name:digest(p) for p in sorted(output.glob('*txt'))}
        else:
            row['errors']=re.findall(r'^Error[^\n]*',(work/'seed-quartus.log').read_text(),re.M)
        save();print(json.dumps({k:v for k,v in row.items() if k in ['seed','LABs','deficit_to_1663','fit_verified','errors','reported_timing']}),flush=True)
    report['complete']=len(report['seeds'])==3
    report['source_identity_verified']=all(digest(Path(p))==sha for p,sha in protected.items());save()
    print(sweep/'report.json',flush=True)
    if not report['source_identity_verified']:raise SystemExit(1)


if __name__=='__main__':main()
