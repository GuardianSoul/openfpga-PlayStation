#!/usr/bin/env python3
"""Check signed/unsigned GHDL Verilog shifts against independent integer values."""
import json
from pathlib import Path
import random
import re
import subprocess

from ghdl_verilog import normalize_generated_verilog
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def main():
    ghdl=find_tool('ghdl');iverilog=find_tool('iverilog');vvp=find_tool('vvp')
    if not all((ghdl,iverilog,vvp)):raise SystemExit('GHDL and Icarus required')
    folder=ROOT/'build/sim/ghdl-shifts';folder.mkdir(parents=True,exist_ok=True)
    source=ROOT/'sim/reference/ghdl_shift_probe.vhd';verilog=folder/'shifts.v'
    generator=random.Random(4106)
    values=[(n-8)<<12 for n in range(16)]+[-(1<<31),(1<<31)-1,-65537,-65536,-32769,-1,0,1,32767,32768,65535,65536]
    values += [generator.randrange(-(1<<31),1<<31) for _ in range(128)]
    rows=[]
    for value in values:
        small=value&65535
        if small&32768:small-=65536
        for amount in range(32):
            rows.append(f'{value&0xffffffff:08x} {amount} {(value>>amount)&0xffffffff:08x} {(small>>amount)&65535:04x} {((value&0xffffffff)>>amount):08x}')
    vectors=folder/'vectors.txt';vectors.write_text('\n'.join(rows)+'\n')
    bench=folder/'tb_shifts.sv'
    bench.write_text('''module tb_shifts;
reg [31:0] value_in=0;
reg [4:0] shift_in=0;
wire [31:0] wide_signed,unsigned_out;
wire [15:0] narrow_signed;
ghdl_shift_probe dut(.*);
reg [31:0] input_value,expected_wide,expected_unsigned;
reg [15:0] expected_narrow;
integer amount,file_handle,parsed,cases=0;
initial begin
 file_handle=$fopen("vectors.txt","r");
 parsed=$fscanf(file_handle,"%h %d %h %h %h",input_value,amount,expected_wide,expected_narrow,expected_unsigned);
 while(parsed==5) begin
  value_in=input_value;shift_in=amount;#1;
  if(wide_signed!==expected_wide || narrow_signed!==expected_narrow || unsigned_out!==expected_unsigned)
   $fatal(1,"GHDL shift mismatch value=%x amount=%d got=%x/%x/%x expected=%x/%x/%x",input_value,amount,wide_signed,narrow_signed,unsigned_out,expected_wide,expected_narrow,expected_unsigned);
  cases++;
  parsed=$fscanf(file_handle,"%h %d %h %h %h",input_value,amount,expected_wide,expected_narrow,expected_unsigned);
 end
 if(cases==0 || !$feof(file_handle)) $fatal(1,"Shift vector parse failed");
 $display("PASS GHDL shifts cases=%d checks=%d",cases,cases*3);$finish;
end
endmodule
''')
    with (folder/'build.log').open('w') as log:
        subprocess.run([ghdl,'-a','--std=08',str(source)],cwd=folder,stdout=log,stderr=log,check=True,timeout=60)
        with verilog.open('w') as out:
            subprocess.run([ghdl,'--synth','--std=08','--out=verilog','ghdl_shift_probe'],cwd=folder,stdout=out,stderr=log,check=True,timeout=60)
        normalization=normalize_generated_verilog(verilog)
        runs=[]
        for label,path in (('raw',Path(normalization['raw_path'])),('normalized',verilog)):
            binary=folder/(label+'.vvp')
            subprocess.run([iverilog,'-g2012','-s','tb_shifts','-o',str(binary),str(path),str(bench)],cwd=folder,stdout=log,stderr=log,check=True,timeout=60)
            result=subprocess.run([vvp,str(binary)],cwd=folder,capture_output=True,text=True,timeout=60)
            marker=re.search(r'PASS GHDL shifts cases=\s*(\d+) checks=\s*(\d+)',result.stdout)
            passed=result.returncode==0 and bool(marker) and int(marker[1])==len(rows) and int(marker[2])==len(rows)*3
            runs.append({'label':label,'exit_code':result.returncode,'passed':passed,'stdout':result.stdout+result.stderr})
    report={'ghdl_version':subprocess.check_output([ghdl,'--version'],text=True).splitlines()[0],
            'normalization':normalization,'source_sha256':digest(source),'bench_sha256':digest(bench),
            'vectors_sha256':digest(vectors),'cases':len(rows),'checks':len(rows)*3,'runs':runs,
            'passed':runs[-1]['passed'],'game_verified':False,'hardware_verified':False}
    (folder/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(report['ghdl_version']);print(runs[-1]['stdout'],end='')
    print('Raw exporter passed:',runs[0]['passed'],'signed operators normalized:',normalization['signed_shift_operator_fixes'])
    if not report['passed']:raise SystemExit('Signed shift conversion failed')


if __name__=='__main__':main()
