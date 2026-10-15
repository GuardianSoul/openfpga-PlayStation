#!/usr/bin/env python3
"""Record source-verified failed full fits and a successful component fabric fit."""
import argparse
import json
from pathlib import Path
import re
import hashlib

if __package__:
    from .prepare_resource_targets import digest
    from .tool_paths import ROOT
else:
    from prepare_resource_targets import digest
    from tool_paths import ROOT


def verify_sources(directory, sources):
    """Preserve exact bytes; later Git updates cannot replace a compiled input.

    Snapshots are accepted only against the compiler manifest's original SHA.
    Callers separately require source identity at completion, so a snapshot
    never excuses source mutation during a running build.
    """
    archive=directory/'compiled-sources'; archive.mkdir(exist_ok=True)
    identities=[{k:s[k] for k in ('path','library','sha256')} for s in sources]
    identity_hash=hashlib.sha256(json.dumps(identities,sort_keys=True).encode()).hexdigest()
    index_path=archive/'index.json'
    if index_path.exists():
        index=json.loads(index_path.read_text())
        if index['source_set_sha256']!=identity_hash:raise ValueError('Snapshot source set mismatch')
    snapshots=[]
    for item in sources:
        path=Path(item['path']); snapshot=archive/(item['sha256']+path.suffix)
        if not snapshot.exists():
            if digest(path)!=item['sha256']:raise ValueError('Changed source without matching snapshot: '+str(path))
            snapshot.write_bytes(path.read_bytes())
        if digest(snapshot)!=item['sha256']:raise ValueError('Changed compiled-source snapshot: '+str(snapshot))
        snapshots.append({'original':str(path),'snapshot':snapshot.name,'sha256':item['sha256']})
    index_path.write_text(json.dumps({'source_set_sha256':identity_hash,'sources':snapshots},indent=2)+'\n')
    return digest(index_path)


def number(text, label):
    match=re.search(r'^'+re.escape(label)+r'\s*:\s*([\d,]+)',text,re.M)
    if not match:raise ValueError('Missing '+label)
    return int(match[1].replace(',',''))


def full_run(directory):
    directory=directory.resolve()
    manifest=json.loads((directory/'manifest.json').read_text())
    compiler_report=json.loads((directory/'quartus-report.json').read_text())
    if not compiler_report['source_identity_verified'] or not compiler_report['project_identity_verified']:
        raise ValueError('Inputs changed during full build')
    snapshot_hash=verify_sources(directory,manifest['sources'])
    for name,sha in manifest['project_hashes'].items():
        if digest(directory/name)!=sha:raise ValueError('Changed project: '+name)
    log=(directory/'quartus.log').read_text(errors='replace')
    required=re.search(r'Error \(170012\): Fitter requires (\d+) LABs .*only (\d+) LABs',log)
    if not required or int(required[2])!=1848:raise ValueError('No device-matched LAB requirement')
    fit=directory/'output_files/apf_top.fit.summary'; text=fit.read_text()
    if not re.search(r'^Fitter Status\s*:\s*Failed',text,re.M) or '5CEBA4F23C8' not in text:
        raise ValueError('Expected failed physical Pocket fit summary')
    mapped=directory/'output_files/apf_top.map.summary'; mapping=mapped.read_text()
    if number(mapping,'Total virtual pins')!=0:raise ValueError('Full fit used virtual pins')
    if not manifest['save_adapter_extensions']:raise ValueError('Pair requires matching save extensions')
    return {'run':str(directory.relative_to(ROOT)),'fit_successful':False,'physical_pins':number(mapping,'Total pins'),
        'virtual_pins':0,'required_labs':int(required[1]),'device_labs':1848,'target_labs':1663,
        'over_device_labs':int(required[1])-1848,'over_target_labs':int(required[1])-1663,
        'provisional_failed_fit_alms':number(text,'Logic utilization (in ALMs)'),
        'fitted_registers':number(text,'Total registers'),
        'synthesized_registers':number(mapping,'Total registers'),
        'memory_bits':number(mapping,'Total block memory bits'),
        'synthesized_dsp_blocks':number(mapping,'Total DSP Blocks'),
        'compile_profile':manifest['compile_profile'],'compile_settings':manifest['compile_settings'],
        'variant_flags':manifest['variant_flags'],'reset_only':manifest['reset_only'],
        'extra_variant_flags':manifest.get('extra_variant_flags',{}),
        'save_adapter_extensions':True,'source_identity_verified':True,'project_identity_verified':True,
        'compiled_source_index_sha256':snapshot_hash,
        'fit_summary_sha256':digest(fit),'map_summary_sha256':digest(mapped),
        'manifest_sha256':digest(directory/'manifest.json'),'project_sha256':manifest['project_hashes'],
        'limitations':manifest['limitations']}


def fabric_run(directory):
    fabric=json.loads((directory/'quartus-report.json').read_text())
    result=fabric['result']
    if not all(result[k] for k in ('fit_verified','source_identity_verified','project_identity_verified')):
        raise ValueError('Fabric fit or identities are not verified')
    fabric_snapshot=verify_sources(directory,result['source_manifest'])
    for suffix,sha in result['project_sha256'].items():
        if digest(directory/(result['top']+suffix))!=sha:raise ValueError('Changed fabric project')
    if digest(Path(result['fit_summary']['path']))!=result['fit_summary']['sha256']:
        raise ValueError('Changed fabric summary')
    fabric_summary=Path(result['fit_summary']['path']).read_text()
    hierarchy=(directory/'output_files'/(result['top']+'.fit.rpt')).read_text(errors='replace')
    attribution=re.search(r'^;\s*\|'+re.escape(result['top'])+r'\s*;\s*([\d.]+) \(([\d.]+)\)',hierarchy,re.M)
    if not attribution:raise ValueError('Missing fabric hierarchy attribution')
    return {'run':str(directory.resolve().relative_to(ROOT)),
        'tool_version':fabric['tool_version'].strip(),
        'fit_verified':True,'counts':result['counts'],'timing':result['reported_timing'],
        'fit_summary_sha256':result['fit_summary']['sha256'],
        'compiled_source_index_sha256':fabric_snapshot,
        'project_sha256':result['project_sha256'],'source_identity_verified':True,
        'project_identity_verified':True,'virtual_interface_alms':number(fabric_summary,'  * ALMs containing virtual pins'),
        'implementation_hierarchy_attributed_alms':round(float(attribution[1])-float(attribution[2]),1),
        'limitations':result['limitations']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--save-on',type=Path,required=True)
    parser.add_argument('--reset-only',type=Path,required=True)
    parser.add_argument('--fabric',type=Path,required=True)
    parser.add_argument('--fabric-before',type=Path,help='Retain a historical fabric measurement for comparison')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    enabled=full_run(args.save_on); disabled=full_run(args.reset_only)
    if enabled['reset_only'] or not disabled['reset_only']:raise ValueError('Reversed save pair')
    for key in ('compile_profile','compile_settings','variant_flags','extra_variant_flags','save_adapter_extensions'):
        if enabled[key]!=disabled[key]:raise ValueError('Configuration mismatch: '+key)
    fabric=fabric_run(args.fabric)
    output={'date':'2026-10-13','device':'5CEBA4F23C8','tool_version':fabric['tool_version'],
        'save_on':enabled,'reset_only':disabled,
        'save_load_rewind_delta':{key:enabled[key]-disabled[key] for key in
            ('required_labs','synthesized_registers','memory_bits','synthesized_dsp_blocks')},
        'fabric':fabric,
        'release_ready':False,'hardware_verified':False,
        'interpretation':'Failed full-fit LAB requirements are measured packing diagnostics; no successful full fit or timing exists. Delta includes save/load/rewind and restore-write/readback logic relative to reset-only.'}
    if args.fabric_before:output['fabric_before_pipeline']=fabric_run(args.fabric_before)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps(output['save_load_rewind_delta']))


if __name__=='__main__':main()
