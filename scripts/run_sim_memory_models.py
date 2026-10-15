#!/usr/bin/env python3
"""Compare host SV memory-model waveforms against the pinned VHDL models."""
import json
from pathlib import Path
import random
import subprocess

from prepare_system_sim import prepare_memory_models
from prepare_resource_targets import UPSTREAM, digest
from tool_paths import ROOT, find_tool

INPUTS=[('refresh',1),('addr',27),('req',1),('ram_dma',1),('ram_dmacnt',2),('ram_iscache',1),
        ('rnw',1),('be',4),('di',32),('ram_dmafifo_adr',23),('ram_dmafifo_data',32),('ram_dmafifo_empty',1),
        ('ddr_rd',1),('ddr_we',1),('ddr_addr',29),('ddr_be',8),('ddr_din',64),('ddr_count',8)]
OUTPUTS=[('do32',32),('done',1),('cache_wr',4),('cache_data',32),('cache_addr',8),('dma_wr',1),
         ('dma_data',32),('reqprocessed',1),('ram_dmafifo_read',1),('ddr_busy',1),('ddr_ready',1),('ddr_dout',64)]
SDR=['refresh','addr','req','ram_dma','ram_dmacnt','ram_iscache','rnw','be','di','do32','done','cache_wr',
     'cache_data','cache_addr','dma_wr','dma_data','reqprocessed','ram_dmafifo_adr','ram_dmafifo_data',
     'ram_dmafifo_empty','ram_dmafifo_read']
DDR={'DDRAM_BUSY':'ddr_busy','DDRAM_BURSTCNT':'ddr_count','DDRAM_ADDR':'ddr_addr','DDRAM_DOUT':'ddr_dout',
     'DDRAM_DOUT_READY':'ddr_ready','DDRAM_RD':'ddr_rd','DDRAM_DIN':'ddr_din','DDRAM_BE':'ddr_be','DDRAM_WE':'ddr_we'}


def fixtures(directory,ticks=6000):
    rng=random.Random(4674629)
    rows=[]
    for tick in range(ticks):
        values={name:0 for name,_ in INPUTS}
        values.update(ram_dmafifo_empty=1,ddr_count=1)
        if tick>=100:
            # Inputs remain stable for three 3x edges (one CPU clock).
            if tick%3==1:
                group=tick//3
                values.update(refresh=int(group%263==0),addr=rng.randrange(512)*2,
                    req=int(group%11==0),ram_dma=rng.randrange(2),ram_dmacnt=rng.randrange(4),
                    ram_iscache=rng.randrange(2),rnw=rng.randrange(2),be=rng.randrange(16),di=rng.getrandbits(32),
                    ram_dmafifo_adr=rng.randrange(256)*4,ram_dmafifo_data=rng.getrandbits(32),
                    ram_dmafifo_empty=int(group%29!=0),ddr_addr=rng.randrange(256),ddr_din=rng.getrandbits(64),
                    ddr_be=rng.choice([0,3,12,15,48,192,240,255,51,204]),ddr_count=rng.randrange(1,9),
                    ddr_rd=int(group%13==0),ddr_we=int(group%7==0 and group%13!=0))
                previous=values.copy()
            else: values=previous.copy() if 'previous' in locals() else values
        rows.append(' '.join(f'{values[name]:0{(width+3)//4}x}' for name,width in INPUTS))
    (directory/'stimulus.txt').write_text('\n'.join(rows)+'\n')
    s='''library ieee;use ieee.std_logic_1164.all;use ieee.numeric_std.all;
use std.textio.all;use std.env.all;library tb;
entity tb_memory_models is end entity;
architecture test of tb_memory_models is
signal clk : std_logic:='1'; signal clk3x : std_logic:='1';
'''
    for name,width in INPUTS+OUTPUTS:
        kind='std_logic' if width==1 else f'std_logic_vector({width-1} downto 0)'
        s+=f'signal {name}: {kind};\n'
    s+='begin\nclk<=not clk after 15 ns;clk3x<=not clk3x after 5 ns;\n'
    s+="ram:entity tb.sdram_model3x generic map(DOREFRESH=>'1',MEMORY_BYTES=>2048) port map(clk=>clk,clk3x=>clk3x,"
    s+=','.join(name+'=>'+name for name in SDR)
    s+=",do=>open,ram_idle=>open,fileSize=>open,exe_initial_pc=>open,exe_initial_gp=>open,exe_load_address=>open,exe_file_size=>open,exe_stackpointer=>open);\n"
    s+='ddr:entity tb.ddrram_model generic map(SLOWTIMING=>15) port map(DDRAM_CLK=>clk3x,'
    s+=','.join(name+'=>'+value for name,value in DDR.items())+');\n'
    s+='''process
file stimuli:text open read_mode is "stimulus.txt";
file output:text open write_mode is "vhdl-trace.txt";
variable row,result:line;
'''
    for name,width in INPUTS:s+=f'variable v_{name}:std_logic_vector({width-1} downto 0);\n'
    s+='begin\nwhile not endfile(stimuli) loop\nreadline(stimuli,row);\n'
    for name,width in INPUTS:
        s+=f'hread(row,v_{name});{name}<=v_{name}'+('(0)' if width==1 else '')+';\n'
    s+='wait until rising_edge(clk3x);wait for 1 ps;\n'
    for name,width in OUTPUTS:s+=f'write(result,to_hstring('+('std_logic_vector\'(0=>'+name+')' if width==1 else name)+'));write(result,string\'(\" \"));\n'
    s+='writeline(output,result);wait until falling_edge(clk3x);\nend loop;finish;end process;end architecture;\n'
    (directory/'tb_memory_models.vhd').write_text(s)
    s='`timescale 1ps/1ps\nmodule tb_memory_models;reg clk=1,clk3x=1;\nalways #15000 clk=~clk;always #5000 clk3x=~clk3x;\n'
    for name,width in INPUTS+OUTPUTS:s+=f'logic [{width-1}:0] {name};\n'
    s+='mister_sdram_model #(.MEMORY_BYTES(2048)) ram(.clk(clk),.clk3x(clk3x),'+','.join('.'+name+'('+name+')' for name in SDR)+');\n'
    s+='mister_ddr_model ddr(.clk(clk3x),'+','.join('.'+name.removeprefix('DDRAM_').lower().replace('dout_ready','ready')+'('+value+')' for name,value in DDR.items())+');\n'
    s+='integer input_file,output_file,count;initial begin\ninput_file=$fopen("stimulus.txt","r");output_file=$fopen("sv-trace.txt","w");\n'
    s+=f'repeat({ticks}) begin\ncount=$fscanf(input_file,"'+(' %h'*len(INPUTS))+'",'+','.join(name for name,_ in INPUTS)+');\n'
    s+=f'if(count!={len(INPUTS)}) $fatal(1,"Stimulus truncated");\n@(posedge clk3x);#1;\n'
    s+='$fwrite(output_file,"'+('%h '*len(OUTPUTS))+'\\n",'+','.join(name for name,_ in OUTPUTS)+');\n@(negedge clk3x);\nend\n$finish;end\nendmodule\n'
    (directory/'tb_memory_models.sv').write_text(s)


def main():
    directory=ROOT/'build/sim/memory-models';directory.mkdir(parents=True,exist_ok=True)
    fixtures(directory);models=prepare_memory_models(directory/'models')
    ghdl=find_tool('ghdl');verilator=find_tool('verilator') or str(ROOT/'build/tools/oss-cad-suite/bin/verilator')
    if not ghdl or not Path(verilator).is_file():raise SystemExit('GHDL and Verilator are required')
    sv=ROOT/'sim/reference/mister_memory_models.sv'
    commands=[[ghdl,'-a','--std=08','--work=tb',str(UPSTREAM/'sim/system/src/tb/globals.vhd'),*[x['path'] for x in models]],
        [ghdl,'-a','--std=08',str(directory/'tb_memory_models.vhd')],
        [ghdl,'-e','--std=08','tb_memory_models'],
        [ghdl,'-r','--std=08','tb_memory_models','--max-stack-alloc=0','--assert-level=error','--ieee-asserts=disable-at-0'],
        [verilator,'--binary','--timing','-j','2','-Wno-fatal','--top-module','tb_memory_models',
         '--Mdir',str(directory/'obj'),str(sv),str(directory/'tb_memory_models.sv')],
        [str(directory/'obj/Vtb_memory_models')]]
    with (directory/'checks.log').open('w') as log:
        for command in commands:
            result=subprocess.run(command,cwd=directory,stdout=log,stderr=subprocess.STDOUT,timeout=120)
            if result.returncode:raise SystemExit(f'Memory model command failed; see {directory}/checks.log')
    old=[line.split() for line in (directory/'vhdl-trace.txt').read_text().splitlines()]
    new=[line.split() for line in (directory/'sv-trace.txt').read_text().splitlines()]
    if len(old)!=6000 or len(new)!=len(old):raise SystemExit('Memory trace lengths differ')
    comparisons=0;unknown=0;activity={name:0 for name,_ in OUTPUTS}
    for tick,(a,b) in enumerate(zip(old,new)):
        if len(a)!=len(OUTPUTS) or len(b)!=len(OUTPUTS):raise SystemExit('Memory trace fields incomplete')
        for (name,_),expected,actual in zip(OUTPUTS,a,b):
            if any(c in expected for c in 'XUZ-'):unknown+=1;continue
            if int(expected,16)!=int(actual,16):raise SystemExit(f'Memory waveform mismatch tick={tick} {name}: VHDL={expected} SV={actual}')
            comparisons+=1
            if int(expected,16):activity[name]+=1
    report={'models':models,'sv_sha256':digest(sv),'stimulus_sha256':digest(directory/'stimulus.txt'),
        'ticks':len(old),'comparisons':comparisons,'upstream_unknown_fields':unknown,'activity':activity,
        'passed':True,'hardware_verified':False,'game_verified':False,
        'internal_dual_port_ram_equivalence_verified':False,
        'scope':'External SDRAM/DDR deterministic waveforms; internal dual-port RAM needs separate coverage'}
    (directory/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(f'PASS memory models ticks={len(old)} comparisons={comparisons} upstream_unknown_fields={unknown}')
    print(activity)


if __name__=='__main__':main()
