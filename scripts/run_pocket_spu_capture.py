#!/usr/bin/env python3
"""Verify completion sideport on the candidate SPU; old outputs cycle-exact.

Reuses the authored CD-mixer oracle and the existing RAM simulation models.
Pauses at the left/right update boundary, mutes, resets and detects planted
early-valid and stretched-valid faults. No guest BIOS/disc inputs.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

from ghdl_verilog import normalize_generated_verilog
from prepare_pocket_audio_stream import capture_spu_text
from prepare_spu_context_ram import context_ram_source
from prepare_spu_reverb_table import reverb_table_text
from prepare_spu_guest_m10k import guest_m10k_text
from prepare_system_verilator import prepare
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio-bridge',action='store_true',
                        help='Also execute the real completion stream through the Pocket bridge')
    args=parser.parse_args()
    parent=ROOT/('build/sim/pocket-spu-bridge' if args.audio_bridge else 'build/sim/pocket-spu-capture')
    parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent))
    ghdl,verilator=find_tool('ghdl'),find_tool('verilator')
    if not ghdl or not verilator:raise ValueError('GHDL and Verilator required')
    manifest=prepare(work/'sources')
    original=next(s for s in manifest['sources'] if Path(s['path']).name=='spu.vhd')
    context=context_ram_source(work/'context',original['path'])
    base_text=reverb_table_text(Path(context['path']).read_text())
    # This existing hardware transform expects the hardware RAM library name.
    # Normalize its guest-table anchors, then restore opaque simulation RAMs.
    base_text=guest_m10k_text(base_text.replace('entity ramip.RamMLAB','entity mem.RamMLAB'))
    # Preserve the preparation's opaque behavioral RAM library for synthesis.
    base_text=base_text.replace('entity mem.dpram','entity ramip.dpram').replace('entity mem.RamMLAB','entity ramip.RamMLAB')
    base=work/'spu_base.vhd';base.write_text(re.sub(r'\bspu\b','spu_base',base_text))
    template=ROOT/'sim/reference/tb_spu_cd_mix.sv';models=ROOT/'sim/reference/mister_memory_models.sv'
    helpers=[ROOT/'scripts'/name for name in ('prepare_pocket_audio_stream.py','prepare_spu_context_ram.py',
             'prepare_spu_reverb_table.py','prepare_spu_guest_m10k.py','prepare_system_verilator.py')]
    protected={str(p):digest(p) for p in (template,models,Path(__file__),*helpers)}
    audio_sources=[]
    coefficients=None
    if args.audio_bridge:
        from generate_pocket_audio_coeffs import generate
        coefficients=generate(work/'coefficients')
        audio_sources=[ROOT/'rtl/audio'/name for name in (
            'psx_audio_sinc.sv','psx_audio_rate.sv','psx_audio_bridge.sv','psx_audio_i2s.sv')]
        audio_bench=ROOT/'sim/pocket/spu_audio_bridge_body.sv'
        for path in (*audio_sources,audio_bench,ROOT/'scripts/generate_pocket_audio_coeffs.py'):
            protected[str(path)]=digest(path)
    def call(command,log,timeout=120):
        subprocess.run(command,cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout)
    with (work/'prepare.log').open('w') as log:
        for record in manifest['sources']:
            call([ghdl,'-a','--std=08','-frelaxed','--work='+record['library'],record['path']],log)
        call([ghdl,'-a','--std=08','-frelaxed',str(base)],log)
        generated_base=work/'spu_base.v'
        with generated_base.open('w') as output:
            subprocess.run([ghdl,'--synth','--std=08','-frelaxed','--vendor-library=ramip','--out=verilog','spu_base'],
                           cwd=work,stdout=output,stderr=log,check=True,timeout=120)
        normalize_generated_verilog(generated_base)
    def header(path,entity):
        text=path.read_text();match=re.search(r'^module ('+entity+r'(?:_Barch[^\s]*)?)[ \t]*\n',text,re.M)
        ports=text[match.start():].split(');',1)[0]
        names={name.lower():name for name in re.findall(r'\b(?:input|output)\s+(?:\[\d+:\d+\]\s+)?(\w+)',ports)}
        outputs={name.lower():size or '' for size,name in re.findall(r'\boutput\s+(\[\d+:\d+\]\s+)?(\w+)',ports)}
        return match[1],names,outputs
    base_header=header(generated_base,'spu_base')
    results=[]
    cases=[
        ('original',lambda t:capture_spu_text(t)),
        ('early-valid',lambda t:capture_spu_text(t).replace("               when 3 =>\n                  sound_out_valid <= '1';",
                          "               when 3 =>\n                  sound_out_valid <= '0';").replace(
                          "               when 2 =>\n                  if (soundmulresult15 < -32768) then sound_out_left",
                          "               when 2 =>\n                  sound_out_valid <= '1';\n                  if (soundmulresult15 < -32768) then sound_out_left")),
        ('stretched-valid',lambda t:capture_spu_text(t).replace("         sound_out_valid <= '0';\n",""))]
    if args.audio_bridge:
        cases.append(('bridge-stereo',capture_spu_text))
    for name,transform in cases:
        case=work/name;case.mkdir();candidate=case/'spu_capture.vhd'
        candidate.write_text(re.sub(r'\bspu\b','spu_capture',transform(base_text)))
        with (case/'build.log').open('w') as log:
            call([ghdl,'-a','--std=08','-frelaxed',str(candidate)],log)
            generated=case/'spu_capture.v'
            with generated.open('w') as output:
                subprocess.run([ghdl,'--synth','--std=08','-frelaxed','--vendor-library=ramip','--out=verilog','spu_capture'],
                               cwd=work,stdout=output,stderr=log,check=True,timeout=120)
            normalize_generated_verilog(generated)
            candidate_header=header(generated,'spu_capture')
            common=set(re.findall(r'^module (\w+)',generated_base.read_text(),re.M))
            chunks=re.findall(r'^module (\w+)(.*?^endmodule\s*$)',generated.read_text(),re.M|re.S)
            generated.write_text('\n'.join('module '+n+b for n,b in chunks if n not in common))
            text=template.read_text()
            instance=re.search(r'SPU_ENTITY spu_core\([^\n]+\);',text).group()
            bindings=re.findall(r'\.(\w+)\((\w+)\)',instance)
            output_nets={net for port,net in bindings if port.lower() in base_header[2]}
            changed=output_nets|{'sdram_dataRead','sdram_done'}
            original_instance=re.sub(r'\.(\w+)\(',lambda m:'.'+base_header[1][m[1].lower()]+'(',instance).replace('SPU_ENTITY',base_header[0])
            candidate_instance=instance.replace('SPU_ENTITY spu_core',candidate_header[0]+' candidate_core')
            candidate_instance=re.sub(r'\.(\w+)\((\w+)\)',lambda m:'.'+candidate_header[1][m[1].lower()]+'('+m[2]+('_candidate' if m[2] in changed else '')+')',candidate_instance)
            candidate_instance=candidate_instance[:-2]+',.'+candidate_header[1]['sound_out_valid']+'(audio_pair_valid));'
            declarations='\n'.join('logic '+base_header[2][port.lower()]+net+'_candidate;' for port,net in bindings if net in output_nets)
            declarations+='\nlogic [31:0] sdram_dataRead_candidate; logic sdram_done_candidate; logic audio_pair_valid;\n'
            text=text.replace(instance,declarations+original_instance+'\n'+candidate_instance)
            memory=re.search(r'mister_sdram_model #\(.*?\) memory\(.*?\);',text,re.S).group()
            second=memory.replace(' memory(',' memory_candidate(')
            second=re.sub(r'\b('+'|'.join(sorted(changed,key=len,reverse=True))+r')\b',lambda m:m[0]+'_candidate',second)
            text=text.replace(memory,memory+'\n'+second)
            monitor='''
integer paired_ticks=0, completed_pairs=0, consumed_pairs=0;
reg expected_pair_valid=0, suspend_period=0;
always @(posedge clk1x) begin
    expected_pair_valid=ce && !reset && spu_core.endprocstep==3;
    if(audio_pair_valid) begin
        if({sound_out_right_candidate,sound_out_left_candidate} !== {sound_out_right,sound_out_left})
            $fatal(1,"SPU completed stereo capture mismatch");
        consumed_pairs++;
    end
end
always @(negedge clk1x) begin
    #1;
    if(audio_pair_valid !== expected_pair_valid) $fatal(1,"SPU completion-valid mismatch");
    if(audio_pair_valid) completed_pairs++;
end
always @(negedge clk2x) begin
    #1; paired_ticks++;
'''+''.join(f'    if({net} !== {net}_candidate) $fatal(1,"SPU old port cycle mismatch {net}");\n' for net in sorted(output_nets))+'end\n'
            text=text.replace('integer checks=0,ticks=0;',monitor+'\ninteger checks=0,ticks=0;')
            if args.audio_bridge:
                bridge_text=audio_bench.read_text()
                if name=='bridge-stereo':
                    bridge_text=bridge_text.replace(
                        '.src_frame({sound_out_right_candidate,sound_out_left_candidate})',
                        '.src_frame({sound_out_left_candidate,sound_out_right_candidate})')
                text=text.replace('integer checks=0,ticks=0;',bridge_text+'\ninteger checks=0,ticks=0;')
            text=text.replace('if(last_tick>=0) begin','if(last_tick>=0 && !suspend_period) begin')
            extra='''
suspend_period=1;
wait(spu_core.endprocstep==3); @(negedge clk1x); ce=0;
repeat(7) @(negedge clk1x); ce=1;
repeat(10) begin @(posedge spu_tick); @(negedge clk1x); end
// Exercise the actual reverb schedule with all voices keyed on (authored
// zero RAM), then stop at the completed-pair pulse. Old ports still lockstep.
REVERBOFF=0;
write_reg(10'h1aa,16'hc081);
write_reg(10'h198,16'hffff);write_reg(10'h19a,16'h00ff);
write_reg(10'h188,16'hffff);write_reg(10'h18a,16'h00ff);
repeat(160) begin @(posedge spu_tick); @(negedge clk1x); end
wait(audio_pair_valid); @(negedge clk1x); ce=0;
repeat(7) @(negedge clk1x); ce=1;
repeat(10) begin @(posedge spu_tick); @(negedge clk1x); end
SPUon=0;
repeat(3) begin @(posedge spu_tick); @(negedge clk1x); end
SPUon=1;
@(negedge clk1x); reset=1; SS_reset=1;
repeat(20) @(negedge clk1x); reset=0; SS_reset=0;
repeat(10) begin @(posedge spu_tick); @(negedge clk1x); end
if(completed_pairs<1500 || consumed_pairs<1500) $fatal(1,"SPU completion coverage");
$display("PASS SPU completion sideport: old_ports=OUTPUT_COUNT ticks=%0d pairs=%0d consumed=%0d CE/mute/reset",paired_ticks,completed_pairs,consumed_pairs);
'''.replace('OUTPUT_COUNT',str(len(output_nets)))
            if args.audio_bridge:
                # Both the left/right boundary and completed-pair pause now
                # last longer than the FIFO's reserve. The run sideport must
                # stop consumption without resetting guest/FIR history.
                extra=extra.replace('repeat(7) @(negedge clk1x); ce=1;',
                                    'repeat(50000) @(negedge clk1x); ce=1;')
                extra+='\ncheck_audio_bridge();\n'
            text=text.replace(';$finish;',';'+extra+'$finish;')
            bench=case/'bench.sv';bench.write_text(text)
            call([verilator,'--binary','--timing','-O3','-j','4','-Wno-fatal','--top-module','tb_spu_mix',
                  '--Mdir',str(case/'obj'),*(['-DCOEFFICIENT_FILE="'+coefficients['compact_path']+'"'] if args.audio_bridge else []),
                  str(generated_base),str(generated),str(models),*[str(p) for p in audio_sources],str(bench)],log,300)
        result=subprocess.run([str(case/'obj/Vtb_spu_mix')],cwd=work,capture_output=True,text=True,timeout=120)
        output=result.stdout+result.stderr;(case/'run.log').write_text(output);print(name+': '+output,end='',flush=True)
        result_record={'name':name,'exit_code':result.returncode,'stdout':output,
            'inputs_sha256':{str(p):digest(p) for p in (candidate,generated_base,generated,bench,models,case/'obj/Vtb_spu_mix')},
            'log_sha256':digest(case/'run.log'),'passed':result.returncode==0 and 'PASS SPU completion sideport' in output
                and (not args.audio_bridge or 'PASS actual SPU audio bridge:' in output),
            'rejected':result.returncode!=0 and (
                'SPU completion-valid mismatch' in output or
                (name=='bridge-stereo' and 'SPU bridge FIFO order' in output))}
        results.append(result_record)
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    identity &= all(digest(Path(s['path']))==s['sha256'] for s in manifest['sources'])
    report={'passed':results[0]['passed'] and all(r['rejected'] for r in results[1:]) and identity,
            'source_identity_verified':identity,'protected_sha256':protected,'baseline_sha256':digest(base),
            'preparation':manifest,'runs':results,'old_port_cycles_changed':False,
            'actual_spu_bridge_executed':args.audio_bridge,'coefficients':coefficients,
            'fit_verified':False,'game_verified':False,'hardware_verified':False}
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work,flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
