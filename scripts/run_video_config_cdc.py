#!/usr/bin/env python3
"""Ordered84-bit video packets: clock/reset/duplicate-state tests and faults.

Uses archived copies of the exact wrapper, pinned FIFO and bench. No BIOS or
game data. The production experiment assumes continuously running clocks in
its guarded rate range; a stopped consumer eventually reports FIFO exhaustion.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def main():
    parent=ROOT/'build/sim/video-config-cdc';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    originals=[ROOT/'rtl/pocket/psx_video_config_cdc.sv',
        ROOT/'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv',ROOT/'sim/pocket/tb_psx_video_config_cdc.sv',
        Path(__file__).resolve(),ROOT/'scripts/tool_paths.py',ROOT/'scripts/prepare_resource_targets.py']
    protected={};archives={}
    for i,source in enumerate(originals):
        sha=digest(source);copy=work/'input-archive'/str(i)/source.name
        copy.parent.mkdir(parents=True);shutil.copyfile(source,copy)
        if digest(copy)!=sha:raise ValueError('Video FIFO source changed during capture')
        archives[str(source)]=dict(path=str(copy),sha256=sha)
        protected[str(source)]=sha;protected[str(copy)]=sha
    rtl,fifo,bench=[Path(archives[str(p)]['path']) for p in originals[:3]]
    runs=[]

    def run(name,source,sh=14.76255,dh=9.31219,phase=0,expected_fault=None):
        directory=work/name;directory.mkdir();binary=directory/'tb.vvp'
        command=[find_tool('iverilog'),'-g2012','-s','tb',f'-Ptb.SH={sh}',f'-Ptb.DH={dh}',f'-Ptb.PHASE={phase}',
            '-o',str(binary),str(source),str(fifo),str(bench)]
        with (directory/'compile.log').open('w') as log:
            compiled=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=60)
        result=dict(name=name,compiler_command=command,compiler_exit_code=compiled.returncode,
            source_sha256=digest(source),expected_fault=expected_fault,accepted=False)
        if compiled.returncode==0:
            log=directory/'run.log'
            with log.open('w') as output:
                completed=subprocess.run([find_tool('vvp'),str(binary)],stdout=output,stderr=subprocess.STDOUT,timeout=60)
            text=log.read_text()
            accepted=(completed.returncode!=0 and expected_fault in text if expected_fault else
                completed.returncode==0 and text.count('PASS VIDEO_CONFIG')==1 and 'FATAL' not in text)
            result.update(exit_code=completed.returncode,output=text,accepted=accepted,
                log_sha256=digest(log),binary_sha256=digest(binary))
        runs.append(result);print(name,'PASS' if result['accepted'] else 'FAIL',flush=True)

    for name,sh,dh,phase in [('ntsc',14.76255,9.31219,0),('shifted',14.76255,9.31219,7.1),
            ('pal',14.76255,9.39789,3.8),('margin',12.5,10,4.2)]:
        run(name,rtl,sh,dh,phase)
    text=rtl.read_text();marker='    dual_clock_fifo #('
    if text.count('.rd_data_o(dst_data)')!=1 or text.count(marker)!=1:
        raise ValueError('Video FIFO corruption anchors changed')
    altered=text.replace('.rd_data_o(dst_data)', '.rd_data_o(corrupted_input)')
    data_bit=altered.replace(marker,"    wire [WIDTH-1:0] corrupted_input;\n    assign dst_data=corrupted_input ^ (84'b1<<29);\n"+marker)
    altered=text.replace('.rd_data_o(dst_data)', '.rd_data_o(coherent_payload)')
    live_controls=altered.replace(marker,'    wire [WIDTH-1:0] coherent_payload;\n    assign dst_data={src_data[WIDTH-1:WIDTH-4],coherent_payload[WIDTH-5:0]};\n'+marker)
    for before in ('src_reset || !destination_online_sync','dst_reset || !source_online_sync'):
        if text.count(before)!=1:raise ValueError('Video FIFO reset control anchor changed')
    old_reset=text.replace('src_reset || !destination_online_sync','src_reset').replace('dst_reset || !source_online_sync','dst_reset')
    for name,changed,expected in [('data-bit',data_bit,'Configuration order/data mismatch'),
            ('live-controls',live_controls,'Configuration order/data mismatch'),
            ('old-reset',old_reset,'Unaccepted configuration returned')]:
        path=work/('fault-'+name+'.sv');path.write_text(changed);protected[str(path)]=digest(path)
        run('fault-'+name,path,expected_fault=expected)
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    passed=identity and len(runs)==7 and all(r['accepted'] for r in runs)
    report=dict(passed=passed,source_identity_verified=identity,source_sha256=protected,input_archives=archives,
        runs=runs,all_accepted_snapshots_preserved=passed,fault_controls_rejected=passed,
        hardware_verified=False,timing_verified=False,cycle_equivalent=False,
        limitations=['Shared asynchronous reset, local release and peer readiness required',
            'Snapshots including duplicate values must not coalesce',
            'Accepted snapshots may be abandoned only by explicit reset',
            'No analog metastability, fitted timing, optimized full-core or game qualification'])
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not passed:raise SystemExit(1)


if __name__=='__main__':main()
