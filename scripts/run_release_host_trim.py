#!/usr/bin/env python3
"""Execute generated bridge projections and Pocket digital levels after cleanup."""
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zlib

from prepare_release_host_trim import apply_release_host_trim,BUTTON_BITS,SERVICE_READS
from prepare_pocket_boot_loader import validate_boot_contract
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def contract(parent,apf=False):
    for path in sorted(parent.glob('checks-*/report.json'),key=lambda p:p.stat().st_mtime,reverse=True):
        try:validate_boot_contract(path,apf);return path
        except (ValueError,KeyError,OSError):pass
    raise ValueError('Run the BIOS loader and APF boot contracts first')


def projection(text,registers,name):
    writes=re.findall(r'    always @\(posedge clk_74a\) begin\n(?:        if\(bridge_wr .*\n)+    end\n',text)
    if len(writes)!=1:raise ValueError('Expected one generated host register block')
    match=re.search(r'    reg \[31:0\] (?:diagnostic|service)_read;\n.*?    wire load_valid',text,re.S)
    if not match:raise ValueError('Generated readback block missing')
    reads=match[0].rsplit('    wire load_valid',1)[0]
    names=set(re.findall(r'(?:diagnostic|service)_read=(\w+);',reads))
    declarations=[]
    for n in sorted(names):
        if not n.isidentifier() or n in ['clk1x','clk2x','clk3x','clkvid']:continue
        matches=re.findall(r'    wire (\[[^\]]+\] )?'+re.escape(n)+r';',text)
        if len(matches)!=1:raise ValueError('Readback type changed: '+n)
        width=matches[0] or ''
        tag=zlib.crc32(n.encode())
        declarations.append(f'    wire {width}{n}=value ^ 32\'h{tag&0xffffffff:08x};')
    output='output wire ['+str(28*32-1)+':0] observed,input wire [31:0] value,output wire [15:0] pad;'
    source=f'''module {name}(input wire clk_74a,clk1x,cpu_locked,bridge_wr,
input wire [31:0] bridge_addr,bridge_wr_data,input wire [15:0] cont1_key,
output reg [31:0] bridge_rd_data,{output[:-1]});
wire clk2x=clk1x,clk3x=clk1x,clkvid=clk1x;
wire [31:0] cmd_bridge_rd_data=value ^ 32'habcdef01;
wire checking=value[0],failed=value[1],passed=value[2],audio_fault_sync=value[3];
wire [1:0] mem_ready74=value[5:4];wire [17:0] loaded_words=value[17:0],checked_words=value[25:8];
'''+ '\n'.join(declarations)+'\n'
    service=[r for r in registers if not r['name'].startswith('joypad1_Key')]
    if len(service)!=28:raise ValueError('Unexpected retained input set')
    for i,r in enumerate(service):
        n=r['name'];bounds=f"[{r['width']-1}:0] " if r['width']>1 else ''
        source+=f"reg {bounds}{n}=0;assign observed[{i*32}+:32]={n};\n"
    for i,button in enumerate(BUTTON_BITS):
        n='joypad1_Key'+button
        declaration=re.search(r'    (reg|wire) '+n+r'=(.*);',text)
        if not declaration:raise ValueError('Pad source missing')
        source+=f'{declaration[1]} {n}={declaration[2]};assign pad[{i}]={n};\n'
    pad=re.search(r'    wire \[15:0\] pocket_pad_buttons;\n    psx_pocket_pad.*?;',text,re.S)
    if pad:source+=pad[0].replace('cont1_key[15:0]','cont1_key')+'\n'
    return source+writes[0]+reads+'endmodule\n'


def bench_text(registers,removed,readbacks):
    services=sorted(readbacks)
    addresses=[r['address'] for r in registers]+services+[r['address'] for r in removed]
    body='''`timescale 1ns/1ps
module tb_release_host_trim;
reg clk_74a=0,clk1x=0,cpu_locked=0,bridge_wr=0;
always #6.735 clk_74a=~clk_74a;always #14.763 clk1x=~clk1x;
reg [31:0] bridge_addr=0,bridge_wr_data=0,value=0;
reg [15:0] cont1_key=0;
wire [895:0] a,b;wire [15:0] pa,pb;wire [31:0] ra,rb;
reference_host old(.clk_74a(clk_74a),.clk1x(clk1x),.cpu_locked(cpu_locked),.bridge_wr(bridge_wr),
.bridge_addr(bridge_addr),.bridge_wr_data(bridge_wr_data),.value(value),.cont1_key(cont1_key),.observed(a),.pad(pa),.bridge_rd_data(ra));
release_host candidate_host(.clk_74a(clk_74a),.clk1x(clk1x),.cpu_locked(cpu_locked),.bridge_wr(bridge_wr),
.bridge_addr(bridge_addr),.bridge_wr_data(bridge_wr_data),.value(value),.cont1_key(cont1_key),.observed(b),.pad(pb),.bridge_rd_data(rb));
integer compares=0;reg [31:0] addresses[0:NUM-1];reg [15:0] expected_pad;
initial begin
ADDR_INIT
 repeat(5) @(negedge clk1x);cpu_locked=1;
 for(integer key=0;key<18;key++) begin
  cont1_key=key<16 ? 16'b1<<key : key==16 ? 16'hffff:0;
  repeat(3) @(negedge clk1x);
  expected_pad=0;
PAD_EXPECT
  if(pb!==expected_pad) $fatal(1,"Pocket button map mismatch");compares++;
 end
 for(integer i=0;i<4000;i++) begin
  @(negedge clk_74a);bridge_addr=addresses[i%NUM];bridge_wr_data=$urandom;value=$urandom;
  bridge_wr=i%3!=0;
  @(posedge clk_74a);#1;
  if(a!==b) $fatal(1,"Required service input changed");compares+=28;
  case(bridge_addr)
READ_CASE
   default: if(rb!==0) $fatal(1,"Removed diagnostic still readable");
  endcase
  compares++;
 end
 @(negedge clk_74a);bridge_wr=0;bridge_addr=32'hf8000010;#1;
 if(rb!==ra || rb!==(value ^ 32'habcdef01)) $fatal(1,"APF command read priority changed");compares++;
 cpu_locked=0;#1;if(pb!==0) $fatal(1,"Pad reset did not assert asynchronously");
 // A held CPU clock keeps the second sampling stage; loss of PLL lock must
 // still clear it. CE is absent: pause does not freeze physical button levels.
 $display("PASS RELEASE HOST TRIM compares=%0d service_inputs=28 pad_buttons=16",compares);$finish;
end
endmodule
'''
    body=body.replace('NUM',str(len(addresses)))
    body=body.replace('ADDR_INIT','\n'.join(f" addresses[{i}]=32'h{a:08x};" for i,a in enumerate(addresses)))
    mapping=[6,4,5,7,14,15,3,2,0,1,9,11,13,8,10,12]
    body=body.replace('PAD_EXPECT','\n'.join(f'  expected_pad[{i}]=cont1_key[{bit}];' for i,bit in enumerate(mapping)))
    body=body.replace('READ_CASE','\n'.join(f"   32'h{a:08x}: if(rb!==ra) $fatal(1,\"Required service readback changed\");" for a in services))
    return body


def main():
    parent=ROOT/'build/sim/release-host-trim';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent))
    commands=[sys.executable,str(ROOT/'scripts/fit_pocket_audio_top.py'),'--preset','current',
        '--release-boot-contract',str(contract(ROOT/'build/sim/pocket-bios-loader')),
        '--apf-boot-contract',str(contract(ROOT/'build/sim/apf-bios-boot',True))]
    r=subprocess.run(commands,cwd=ROOT,capture_output=True,text=True,check=True,timeout=120)
    base=Path(r.stdout.strip());manifest=json.loads((base/'manifest.json').read_text())
    original=(base/'core_top.sv').read_text();registers=copy.deepcopy(manifest['diagnostic_input_registers'])
    # Preserve the baseline; apply the actual preparation to a new private copy.
    clone=work/'project';shutil.copytree(base,clone)
    for s in manifest['sources']:
        if s['path']==str(base/'core_top.sv'):s['path']=str(clone/'core_top.sv')
    qsf=clone/'apf_top.qsf';qsf.write_text(qsf.read_text().replace(str(base/'core_top.sv'),str(clone/'core_top.sv')))
    manifest['project_hashes']['apf_top.qsf']=digest(qsf)
    manifest=apply_release_host_trim(manifest,clone);trimmed=(clone/'core_top.sv').read_text()
    matches=re.findall(r"32'h([0-9a-f]+): service_read=(.*);",trimmed)
    retained={int(a,16) for a,e in matches}|{0x10000000}
    removed=manifest['release_host_trim']['removed_readbacks']
    baseline=work/'old.sv';baseline.write_text(projection(original,registers,'reference_host'))
    candidate=work/'new.sv';candidate.write_text(projection(trimmed,registers,'release_host'))
    bench=work/'tb.sv';bench.write_text(bench_text(registers,removed,retained))
    pad=ROOT/'rtl/pocket/psx_pocket_pad.sv'
    protected={str(p):digest(p) for p in [Path(__file__),ROOT/'scripts/prepare_release_host_trim.py',pad,baseline,candidate,bench,base/'core_top.sv',clone/'core_top.sv']}
    runs=[]
    def run(name,text=None,marker=None):
        d=work/name;d.mkdir();selected=candidate
        if text is not None:selected=d/'new.sv';selected.write_text(text)
        binary=d/'sim.vvp'
        r=subprocess.run([find_tool('iverilog'),'-g2012','-s','tb_release_host_trim','-o',str(binary),str(baseline),str(selected),str(pad),str(bench)],capture_output=True,text=True,timeout=120)
        (d/'build.log').write_text(r.stdout+r.stderr)
        if r.returncode:raise ValueError('Host projection compilation failed: '+str(d/'build.log'))
        r=subprocess.run([find_tool('vvp'),str(binary)],capture_output=True,text=True,timeout=120);output=r.stdout+r.stderr
        passed=r.returncode!=0 and marker in output if marker else r.returncode==0 and 'FATAL' not in output and output.count('PASS RELEASE HOST TRIM')==1
        runs.append(dict(name=name,passed=passed,exit_code=r.returncode,output=output,binary_sha256=digest(binary)))
        print(name,passed,output,flush=True);return passed
    if run('original'):
        text=candidate.read_text();before='memcard1_write<=bridge_wr_data[0:0]'
        if text.count(before)!=1:raise ValueError('Card fault anchor changed')
        run('lost-card-write',text.replace(before,'memcard1_write<=0'),'Required service input changed')
        before="32'h120000bc: service_read=cd_hps_req;"
        if text.count(before)!=1:raise ValueError('CD fault anchor changed')
        run('lost-CD-request',text.replace(before,"32'h120000bc: service_read=0;"),'Required service readback changed')
        before='wire joypad1_KeyCircle=pocket_pad_buttons[4];'
        run('wrong-button',text.replace(before,'wire joypad1_KeyCircle=pocket_pad_buttons[5];'),'Pocket button map mismatch')
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    report=dict(passed=identity and len(runs)==4 and all(x['passed'] for x in runs),source_identity_verified=identity,
        protected_sha256=protected,runs=runs,release_host_trim=manifest['release_host_trim'],
        whole_core_execution_verified=False,service_transport_CDC_verified=False,hardware_verified=False,
        scope='Executed actual generated bridge fragments and pad RTL; CD/card transport unchanged, game/hardware/physical timing not proved')
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
