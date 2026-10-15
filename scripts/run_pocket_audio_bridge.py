#!/usr/bin/env python3
"""Check stereo CDC, FIR stream order, pause/reset and rate-servo stability."""
import json
from pathlib import Path
import subprocess
import tempfile

from generate_pocket_audio_coeffs import generate
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def main():
    compiler,runtime=find_tool('iverilog'),find_tool('vvp')
    if not compiler or not runtime: raise ValueError('Icarus required')
    parent=ROOT/'build/sim/pocket-audio-bridge'; parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent))
    coefficients=generate(work/'coefficients')
    sources=[ROOT/'rtl/audio'/name for name in ('psx_audio_sinc.sv','psx_audio_rate.sv','psx_audio_bridge.sv','psx_audio_i2s.sv')]
    benches=[ROOT/'sim/pocket'/name for name in ('tb_audio_rate.sv','tb_audio_bridge.sv')]
    protected={str(p):digest(p) for p in (*sources,*benches,Path(__file__),ROOT/'scripts/generate_pocket_audio_coeffs.py')}
    def run(name,top,inputs,bench):
        binary=work/(name+'.vvp')
        command=[compiler,'-g2012','-s',top,'-o',str(binary),
                 '-DCOEFFICIENT_FILE="'+coefficients['compact_path']+'"',
                 '-DFULL_COEFFICIENT_FILE="'+coefficients['path']+'"',*[str(p) for p in inputs],str(bench)]
        with (work/(name+'-build.log')).open('w') as log:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
        result=subprocess.run([runtime,str(binary)],capture_output=True,text=True,timeout=180)
        output=result.stdout+result.stderr; log=work/(name+'.log'); log.write_text(output)
        print(name+': '+output,end='',flush=True)
        return {'exit_code':result.returncode,'stdout':output,'log_sha256':digest(log),
                'passed':result.returncode==0 and ('PASS audio '+('rate' if top=='tb_audio_rate' else 'bridge')) in output}
    rate=run('rate','tb_audio_rate',[sources[1]],benches[0])
    bridge=run('bridge','tb_audio_bridge',sources,benches[1])
    mutations=[]
    low_reserve=work/'low-reserve.sv';low_reserve.write_text(sources[1].read_text().replace("9'sd32","9'sd24"))
    low_bench=work/'low-reserve-bench.sv';low_bench.write_text(benches[0].read_text().replace('=32','=24'))
    low_result=run('low-reserve','tb_audio_rate',[low_reserve],low_bench);low_result['name']='low-reserve'
    low_result['rejected']=low_result['exit_code']!=0 and 'Drift queue failed' in low_result['stdout']
    mutations.append(low_result)
    for name,index,before,after,top,bench in (
        ('bad-rate-sign',1,'proportional + integral','-proportional + integral','tb_audio_rate',benches[0]),
        ('bad-stereo',2,'packets[wr_bin[5:0]]<=src_frame;',
         'packets[wr_bin[5:0]]<={src_frame[15:0],src_frame[31:16]};','tb_audio_bridge',benches[1]),
        ('bad-reset',2,'wr_bin<=0; wr_gray<=0;',
         'wr_bin<=1; wr_gray<=1;','tb_audio_bridge',benches[1]),
        ('sticky-underflow',2,'if(running_sync && !fatal_fault)',
         'if(running_sync && !fault)','tb_audio_bridge',benches[1])):
        original=sources[index].read_text()
        if original.count(before)!=1: raise ValueError('Mutation anchor')
        mutated=work/(name+'.sv'); mutated.write_text(original.replace(before,after))
        inputs=list(sources); inputs[index]=mutated
        if top=='tb_audio_rate': inputs=[mutated]
        result=run(name,top,inputs,bench); result['name']=name
        result['rejected']=result['exit_code']!=0 and ('FATAL' in result['stdout'])
        mutations.append(result)
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    report={'passed':rate['passed'] and bridge['passed'] and identity and all(m['rejected'] for m in mutations),
            'source_identity_verified':identity,'protected_sha256':protected,'coefficients':coefficients,
            'rate':rate,'bridge':bridge,'mutations':mutations,'fit_verified':False,
            'timing_verified':False,'source_spu_capture_verified':False,'hardware_verified':False}
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n'); print(work,flush=True)
    if not report['passed']: raise SystemExit(1)


if __name__=='__main__': main()
