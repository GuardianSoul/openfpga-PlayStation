#!/usr/bin/env python3
"""Compare asynchronous and mixed-width internal RAMs with pinned VHDL models."""
import json
import random
import subprocess
from pathlib import Path

from prepare_resource_targets import UPSTREAM,digest
from tool_paths import ROOT,find_tool

INPUTS=[('ena',1),('enb',1),('wea',1),('web',1),('addra',5),('addrb',5),('da',32),('db',32),
        ('wideaddr',4),('smalladdr',6),('wide',32),('narrow_data',8)]
OUTPUTS=[('mlab',32),('qa',32),('qb',32),('wideqa',32),('smallqb',8),('smallqa',8),('wideqb',32)]


def fixtures(p):
    r=random.Random(15690874)
    rows=[]
    for _ in range(1200):
        row=[r.randrange(2) for _ in range(4)]+[r.randrange(32),r.randrange(32),r.getrandbits(32),r.getrandbits(32),
            r.randrange(16),r.randrange(64),r.getrandbits(32),r.getrandbits(8)]
        rows.append(' '.join(f'{value:0{(width+3)//4}x}' for value,(_,width) in zip(row,INPUTS)))
    (p/'ram-stimulus.txt').write_text('\n'.join(rows)+'\n')
    v='''library ieee;use ieee.std_logic_1164.all;use ieee.numeric_std.all;use std.textio.all;use std.env.all;
entity tb_internal_ram is end entity;
architecture test of tb_internal_ram is
signal a,b:std_logic:='0';
'''
    for name,width in INPUTS+OUTPUTS:v+=f'signal {name}:std_logic_vector({width-1} downto 0):=(others=>\'0\');\n'
    v+='''begin
a<=not a after 5 ns;
process begin wait for 1 ns;loop wait for 7 ns;b<=not b;end loop;end process;
ml:entity work.RamMLAB generic map(width=>32,widthad=>5) port map(inclock=>a,wren=>wea(0),data=>da,wraddress=>addra,rdaddress=>addrb,q=>mlab);
ram:entity work.dpram generic map(addr_width=>5,data_width=>32) port map(clock_a=>a,clken_a=>ena(0),address_a=>addra,data_a=>da,wren_a=>wea(0),q_a=>qa,
 clock_b=>b,clken_b=>enb(0),address_b=>addrb,data_b=>db,wren_b=>web(0),q_b=>qb);
wide_a:entity work.dpram_dif generic map(addr_width_a=>4,data_width_a=>32,addr_width_b=>6,data_width_b=>8)
 port map(clock_a=>a,clken_a=>ena(0),address_a=>wideaddr,data_a=>wide,wren_a=>wea(0),q_a=>wideqa,
 clock_b=>b,clken_b=>enb(0),address_b=>smalladdr,data_b=>narrow_data,wren_b=>web(0),q_b=>smallqb);
wide_b:entity work.dpram_dif generic map(addr_width_a=>6,data_width_a=>8,addr_width_b=>4,data_width_b=>32)
 port map(clock_a=>a,clken_a=>ena(0),address_a=>smalladdr,data_a=>narrow_data,wren_a=>wea(0),q_a=>smallqa,
 clock_b=>b,clken_b=>enb(0),address_b=>wideaddr,data_b=>wide,wren_b=>web(0),q_b=>wideqb);
process
file input:text open read_mode is "ram-stimulus.txt";
file output:text open write_mode is "vhdl-trace.txt";
variable row,result:line;
'''
    for name,width in INPUTS:v+=f'variable v_{name}:std_logic_vector({width-1} downto 0);\n'
    v+='begin\nwhile not endfile(input) loop\nwait until falling_edge(a);wait for 1 ps;readline(input,row);\n'
    for name,_ in INPUTS:v+=f'hread(row,v_{name});{name}<=v_{name};\n'
    v+='wait for 1 ps;\n'
    for name,_ in OUTPUTS:v+=f'write(result,to_hstring({name}));write(result,string\'(\" \"));\n'
    v+='writeline(output,result);end loop;finish;end process;end architecture;\n'
    (p/'tb_internal_ram.vhd').write_text(v)
    s='''`timescale 1ps/1ps
module tb_internal_ram;reg a=0,b=0;
always #5000 a=~a;initial begin #1000;forever #7000 b=~b;end
'''
    for name,width in INPUTS+OUTPUTS:s+=f'logic [{width-1}:0] {name}=0;\n'
    # Older Verilator scheduling misses combinational readers when $fscanf
    # writes a signal directly. Read temporaries, then explicitly drive every
    # signal so asynchronous RAM output updates occur in the same time step.
    for name,width in INPUTS:s+=f'logic [{width-1}:0] fetched_{name};\n'
    s+='''RamMLAB #(.width(32),.widthad(5)) ml(.inclock(a),.wren(wea),.data(da),.wraddress(addra),.rdaddress(addrb),.q(mlab));
dpram #(.addr_width(5),.data_width(32)) ram(.clock_a(a),.clken_a(ena),.address_a(addra),.data_a(da),.wren_a(wea),.q_a(qa),
 .clock_b(b),.clken_b(enb),.address_b(addrb),.data_b(db),.wren_b(web),.q_b(qb));
dpram_dif #(.addr_width_a(4),.data_width_a(32),.addr_width_b(6),.data_width_b(8)) wide_a(
 .clock_a(a),.clken_a(ena),.address_a(wideaddr),.data_a(wide),.wren_a(wea),.q_a(wideqa),
 .clock_b(b),.clken_b(enb),.address_b(smalladdr),.data_b(narrow_data),.wren_b(web),.q_b(smallqb));
dpram_dif #(.addr_width_a(6),.data_width_a(8),.addr_width_b(4),.data_width_b(32)) wide_b(
 .clock_a(a),.clken_a(ena),.address_a(smalladdr),.data_a(narrow_data),.wren_a(wea),.q_a(smallqa),
 .clock_b(b),.clken_b(enb),.address_b(wideaddr),.data_b(wide),.wren_b(web),.q_b(wideqb));
integer input_file,output_file,count;
initial begin
 input_file=$fopen("ram-stimulus.txt","r");output_file=$fopen("sv-trace.txt","w");
 repeat(1200) begin @(negedge a);#1;
'''
    s+='count=$fscanf(input_file,"'+(' %h'*len(INPUTS))+'",'+','.join('fetched_'+name for name,_ in INPUTS)+');\n'
    s+=f'if(count!={len(INPUTS)}) $fatal(1,"Internal RAM stimulus truncated");\n'
    for name,_ in INPUTS:s+=f'{name}=fetched_{name};\n'
    s+='#1;\n'
    s+='$fwrite(output_file,"'+('%h '*len(OUTPUTS))+'\\n",'+','.join(name for name,_ in OUTPUTS)+');\nend\n$finish;end\nendmodule\n'
    (p/'tb_internal_ram.sv').write_text(s)


def main():
    p=ROOT/'build/sim/internal-ram-models';p.mkdir(parents=True,exist_ok=True);fixtures(p)
    g=find_tool('ghdl');v=find_tool('verilator') or str(ROOT/'build/tools/oss-cad-suite/bin/verilator')
    originals=[UPSTREAM/'sim/system/src/mem'/name for name in ('RamMLAB.vhd','dpram.vhd')]
    model=ROOT/'sim/reference/mister_memory_models.sv'
    commands=[[g,'-a','--std=08','-frelaxed',*[str(x) for x in originals],str(p/'tb_internal_ram.vhd')],
        [g,'-e','--std=08','-frelaxed','tb_internal_ram'],
        [g,'-r','--std=08','-frelaxed','tb_internal_ram','--assert-level=error','--ieee-asserts=disable-at-0'],
        [v,'--binary','--timing','-j','2','-Wno-fatal','--top-module','tb_internal_ram','--Mdir',str(p/'obj'),str(model),str(p/'tb_internal_ram.sv')],
        [str(p/'obj/Vtb_internal_ram')]]
    with (p/'checks.log').open('w') as log:
        for cmd in commands:
            r=subprocess.run(cmd,cwd=p,stdout=log,stderr=subprocess.STDOUT,timeout=120)
            if r.returncode:raise SystemExit(f'Internal RAM check failed; see {p}/checks.log')
    old=[x.split() for x in (p/'vhdl-trace.txt').read_text().splitlines()]
    new=[x.split() for x in (p/'sv-trace.txt').read_text().splitlines()]
    if len(old)!=1200 or len(new)!=1200:raise SystemExit('Internal RAM trace incomplete')
    checks=0;unknown=0
    for tick,(a,b) in enumerate(zip(old,new)):
        if len(a)!=7 or len(b)!=7:raise SystemExit('Internal RAM trace fields incomplete')
        for (name,_),expected,actual in zip(OUTPUTS,a,b):
            if any(c in expected for c in 'XUZ-'):unknown+=1;continue
            if int(expected,16)!=int(actual,16):raise SystemExit(f'Internal RAM mismatch tick={tick} {name}: VHDL={expected} SV={actual}')
            checks+=1
    (p/'report.json').write_text(json.dumps({'originals':[{'path':str(x),'sha256':digest(x)} for x in originals],
        'sv_sha256':digest(model),'stimulus_sha256':digest(p/'ram-stimulus.txt'),'checks':checks,
        'unknown_fields':unknown,'passed':True,'hardware_verified':False,
        'scope':'1:1 and 4:1/1:4 widths, distinct clock edges, enables/write-first/cross-port visibility; no coincident write collisions'},indent=2)+'\n')
    print(f'PASS internal RAM models samples=1200 checks={checks} unknown_fields={unknown}')


if __name__=='__main__':main()
