#!/usr/bin/env python3
"""Check atomic analog/menu packets and legacy sampler timing under async clocks."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def tool(name):
    found=find_tool(name)
    fallback=ROOT/'build/tools/oss-cad-suite/bin'/name
    if not found and os.access(fallback,os.X_OK):found=str(fallback)
    if not found:raise RuntimeError(name+' required')
    return found


def main():
    parent=ROOT/'build/sim/pocket-analog-packet';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent))
    source=ROOT/'rtl/pocket/psx_pocket_analog.sv'
    reference=ROOT/'sim/pocket/psx_pocket_analog_ref.sv'
    bench=ROOT/'sim/pocket/tb_psx_pocket_analog_packet.sv'
    protected={str(p):digest(p)for p in [source,reference,bench,Path(__file__)]}
    archive=[]
    folder=work/'inputs';folder.mkdir()
    for path,sha in protected.items():
        copied=folder/Path(path).name;shutil.copyfile(path,copied)
        if digest(copied)!=sha:raise RuntimeError('Input copy changed')
        archive.append(dict(source=path,path=str(copied),sha256=sha))
    report=dict(passed=False,work=str(work),protected_sha256=protected,input_archive=archive,runs=[],
        scope='13- and 15-bit settings, three async clock ratios; two resets while transfers are active and a stopped destination clock. Original lower33-bit sampler checked every destination edge,46/48-bit outputs checked against independent publication tuples; all four top-two-bit values delivered for15-bit mode. Not electrical/metastability or Pocket proof.')
    text=source.read_text();anchor='config_out<=hold[CONFIG_W+32:33];'
    if text.count(anchor)!=1:raise ValueError('Analog packet capture changed')
    modes=('normal','wrong-config','live-config','unknown-config','truncated-config')
    cases=[(width,mode)for width in (13,15)for mode in modes
           if width==15 or mode!='truncated-config']
    for width,mode in cases:
        changed=text
        if mode=='wrong-config':changed=changed.replace(anchor,'config_out<=PASS_CONFIG ? hold[CONFIG_W+31:32] : hold[CONFIG_W+32:33];')
        if mode=='live-config':changed=changed.replace(anchor,'config_out<=PASS_CONFIG ? config_in : hold[CONFIG_W+32:33];')
        if mode=='unknown-config':changed=changed.replace(anchor,"config_out<=PASS_CONFIG ? {CONFIG_W{1'bx}} : hold[CONFIG_W+32:33];")
        if mode=='truncated-config':changed=changed.replace(anchor,'config_out<=hold[45:33];')
        candidate=work/f'{width}-{mode}.sv';candidate.write_text(changed)
        profiles=[(6734,14763),(5000,15500),(11500,8500)]if mode=='normal'else[(6734,14763)]
        for src,dst in profiles:
            name=f'{width}-{mode}-{src}-{dst}';exe=work/name
            command=[tool('iverilog'),'-g2012','-s','tb',f'-Ptb.CONFIG_W={width}',f'-Ptb.SRC_HALF={src}',f'-Ptb.DST_HALF={dst}','-o',str(exe),str(bench),str(reference),str(candidate)]
            built=subprocess.run(command,capture_output=True,text=True,timeout=60)
            (work/(name+'-build.log')).write_text(built.stdout+built.stderr)
            if built.returncode:raise RuntimeError(built.stdout+built.stderr)
            result=subprocess.run([tool('vvp'),str(exe)],capture_output=True,text=True,timeout=60)
            output=result.stdout+result.stderr;log=work/(name+'.log');log.write_text(output)
            ok=(result.returncode==0 and output.count('PASS atomic pad ')==1)if mode=='normal'else(result.returncode!=0 and 'torn config/analog packet'in output)
            report['runs'].append(dict(mode=mode,config_width=width,src_half_ps=src,dst_half_ps=dst,exit_code=result.returncode,expected_verdict=ok,log=str(log),sha256=digest(log),metrics={k:int(v)for k,v in re.findall(r'(checks|deliveries|published|resets|face_modes)=(\d+)',output)}))
            (work/'report.json').write_text(json.dumps(report,indent=2)+'\n')
            print(name,'PASS'if ok else'FAIL',flush=True)
            if not ok:raise RuntimeError(output)
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    report.update(passed=identity and len(report['runs'])==13,source_identity_verified=identity)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['passed']:raise RuntimeError('Analog inputs changed during checks')
    print('PASS pocket analog',work/'report.json',flush=True)


if __name__=='__main__':main()
