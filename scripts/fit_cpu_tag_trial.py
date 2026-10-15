#!/usr/bin/env python3
"""Matched CPU-only fits for the tested, unselected itagram read-phase trial."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_no_turbo_cache import apply_no_turbo_cache
from prepare_resource_targets import apply_compile_profile, digest, prepare
from run_cpu_cache_contract import tag_trial
from run_resource_fit import fit_target
from tool_paths import ROOT, find_tool


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contract-report', type=Path, required=True)
    args=parser.parse_args()
    contract=json.loads(args.contract_report.read_text())
    if not all(contract.get(key) for key in ('passed','fault_coverage_complete',
        'falling_edge_cycle_equivalence_verified','registered_address_model_verified','all_sources_unchanged')):
        raise ValueError('complete CPU cache/phase contract proof required')
    for path,expected in contract['source_sha256'].items():
        if digest(Path(path))!=expected:
            raise ValueError('contract input changed: '+path)
    parent=ROOT/'build/resources/cpu-tag-trial/runs'; parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),dir=parent))
    binary=Path(find_tool('quartus_sh')).parent
    result={'contract_report':str(args.contract_report.resolve()),'contract_report_sha256':digest(args.contract_report),
            'script_sha256':digest(Path(__file__)),'results':{},'selected':False,
            'connected_LAB_saving_measured':False,'board_timing_verified':False,
            'tool_version':subprocess.check_output([str(binary/'quartus_sh'),'--version'],text=True)}
    print(work,flush=True)
    try:
        for mode in ('original','falling'):
            manifest=apply_no_turbo_cache(prepare(work/mode,['cpu']))
            target=manifest['targets']['cpu']
            if mode=='falling':
                matches=[item for item in target['quartus_sources'] if Path(item['path']).name=='cpu.vhd']
                if len(matches)!=1: raise ValueError('expected one selected CPU source')
                previous=matches[0]
                variant=Path(target['directory'])/'cpu-tag-trial.vhd'
                variant.write_text(tag_trial(Path(previous['path']).read_text(),'falling'))
                qsf=Path(target['directory'])/(target['top']+'.qsf')
                text=qsf.read_text()
                if text.count(previous['path'])!=1: raise ValueError('expected one CPU QSF assignment')
                qsf.write_text(text.replace(previous['path'],str(variant)))
                target['project_sha256']['.qsf']=digest(qsf)
                previous.update(path=str(variant),sha256=digest(variant))
                target['limitations']=['Unselected tag-phase storage experiment; bounded contract only',
                    'No full-system LAB saving, external IO timing, PLL/reset or Pocket execution proof']
            manifest=apply_compile_profile(manifest,'area-min-state')
            manifest_file=work/mode/'manifest.json'
            manifest_file.write_text(json.dumps(manifest,indent=2)+'\n')
            result['results'][mode]=fit_target(binary,target,manifest['device'],1800)
            fitted=result['results'][mode]
            if fitted['fit_verified']:
                project=Path(target['directory'])
                rpt=project/'output_files'/(target['top']+'.fit.rpt')
                text=rpt.read_text(errors='replace')
                labs=re.search(r'Total LABs:.*?;\s*(\d+) / 1,848',text)
                mlab=re.search(r'Total MLAB memory bits\s*;\s*([\d,]+)',text)
                if not labs or not mlab: raise ValueError('missing CPU LAB/MLAB counts')
                fitted['partially_or_completely_used_LABs']=int(labs[1])
                fitted['MLAB_memory_bits']=int(mlab[1].replace(',',''))
                fitted['fit_rpt_sha256']=digest(rpt)
                if mode=='falling':
                    ram_rows=[line for line in text.splitlines() if 'itagram|' in line and '|ALTSYNCRAM ' in line]
                    if len(ram_rows)!=1: raise ValueError('missing or ambiguous fitted tag RAM')
                    fields=[x.strip() for x in ram_rows[0].split(';') if x.strip()]
                    if fields[4:8]!=['256','24','256','24'] or fields[12]!='6144' or int(fields[18])!=2:
                        raise ValueError('unexpected tag RAM geometry/cost')
                    fitted['tag_ram_inference']={'row':ram_rows[0], 'words':256,'width':24,'M10Ks':2}
                    script=ROOT/'scripts/report_cpu_tag_trial.tcl'
                    audit_dir=work/'tag-audit'; qsf=project/(target['top']+'.qsf'); before=digest(qsf)
                    audit=subprocess.run([str(binary/'quartus_sta'),'-t',str(script),str(audit_dir)],
                        cwd=project,capture_output=True,text=True,timeout=120)
                    output=audit.stdout+audit.stderr
                    log=work/'tag-audit.log'; log.write_text(output)
                    corners=re.findall(r'CPU_TAG_CORNER (\d+) (\w+)',output)
                    files=list(audit_dir.glob('*.txt'))
                    scope=re.search(r'CPU_TAG_REGISTER_COUNT (\d+)',output)
                    slacks=[]; half_cycle=True
                    for path in files:
                        content=path.read_text(errors='replace')
                        slack=re.search(r'Worst case slack is (-?\d+(?:\.\d+)?)',content)
                        if not slack: raise ValueError('missing CPU tag audit paths: '+str(path))
                        slacks.append(float(slack[1]))
                        if 'setup' in path.name:
                            relationships=re.findall(r'; Setup Relationship\s*;\s*([\d.]+)',content)
                            half_cycle &= bool(relationships) and all(14.75<float(x)<14.78 for x in relationships)
                            half_cycle &= 'cpu_clk (INVERTED)' in content
                    fitted['tag_audit']={'exit_code':audit.returncode,'corners':corners,
                        'register_count':int(scope[1]) if scope else 0,'slacks_ns':slacks,
                        'half_cycle_relationship_verified':half_cycle,
                        'files_sha256':{str(path):digest(path) for path in files},
                        'script_sha256':digest(script),'log_sha256':digest(log),
                        'project_unchanged':digest(qsf)==before,
                        'passed':audit.returncode==0 and bool(corners) and len(files)==4*len(corners)
                            and bool(scope) and int(scope[1])>0 and bool(slacks) and min(slacks)>=0
                            and half_cycle and digest(qsf)==before}
            print(json.dumps({'mode':mode,**{key:result['results'][mode].get(key) for key in (
                'fit_verified','counts','errors','reported_timing')}}),flush=True)
        result['passed']=all(row['fit_verified'] and not row['reported_timing']['timing_violations_reported']
            for row in result['results'].values()) and result['results']['falling']['tag_audit']['passed']
        if not result['passed']: raise RuntimeError('CPU-only fit failed; no saving accepted')
    finally:
        (work/'report.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__': main()
