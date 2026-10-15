#!/usr/bin/env python3
"""Compare a native PSRAM adapter against MiSTer's pinned memory-port model."""
import json
from pathlib import Path
import random
import shutil
import subprocess
import tempfile

from prepare_psram_burst import prepare
from prepare_resource_targets import UPSTREAM,digest
from prepare_system_sim import prepare_memory_models
from run_main_memory import chip_text,bench_text
from tool_paths import ROOT,find_tool


def vectors(directory):
    rng=random.Random(52731);rows=[];gold=[];memory={}
    def add(addr,rnw=1,cache=0,dma=0,count=0,data=0,be=15):
        idx=len(rows);rows.append((addr,rnw,cache,dma,count,data,be))
        if not rnw:
            for b in range(4):
                if be>>b&1:memory[addr+b]=data>>(8*b)&255
            gold.append((idx,'W',0,0,0));return
        result=[]
        for word in range(4):
            a=(addr&~1023)|((addr+word*4)&1023)
            result.append(sum(memory.get(a+b,0)<<(8*b) for b in range(4)))
        if dma:
            for word in range(count+1):gold.append((idx,'D',0,0,result[word]))
        else:
            gold.append((idx,'S',0,0,result[0]))
            if cache:
                for word in range(4-(addr//4%4)):
                    gold.append((idx,'C',1<<(word+addr//4%4),addr//16%256,result[word]))
    for base,words in [(0,1024),(0x800000,512)]:
        for i in range(words):add(base+i*4,0,data=rng.getrandbits(32))
    for i in range(16):
        add(0x100+i*4,0,data=rng.getrandbits(32),be=i)
        add(0x100+i*4)
    for base in [0,0x30,0x3f0,0x7f0,0xbf0]:
        for critical in range(4):add(base+critical*4,cache=1)
    for base in [0,0x3f0,0x7f0,0xbf0]:
        for critical in range(4):
            for count in range(4):add(base+critical*4,dma=1,count=count)
    for i in range(512):
        a=rng.randrange(1024)*4
        if i%5==0:add(a,0,data=rng.getrandbits(32),be=rng.randrange(16))
        elif i%5==1:add(a,cache=1)
        elif i%5==2:add(a,dma=1,count=rng.randrange(4))
        elif i%5==3:add(0x800000+rng.randrange(512)*4)
        else:add(a)
    add(0,dma=1,count=3);add(0x40,dma=1,count=3)
    (directory/'vectors.txt').write_text(''.join(' '.join(f'{x:08x}' if i in (0,5) else f'{x:x}' for i,x in enumerate(r))+'\n' for r in rows))
    # Consumer order is fixed per class; S may precede the last C in upstream.
    gold.sort(key=lambda e:(e[0],e[1]))
    return rows,gold


def oracle_text():
    return '''library ieee;use ieee.std_logic_1164.all;use ieee.numeric_std.all;
use std.textio.all;use std.env.all;library tb;
entity tb_mister_oracle is end;
architecture test of tb_mister_oracle is
signal clk,clk3x:std_logic:='0';
signal addr:std_logic_vector(26 downto 0):=(others=>'0');
signal req,rnw,cache,dma,done,dma_wr,ack,fifo_read:std_logic:='0';
signal cnt:std_logic_vector(1 downto 0):=(others=>'0');
signal be,cwr:std_logic_vector(3 downto 0):=(others=>'0');
signal di,dout,cdata,ddata:std_logic_vector(31 downto 0):=(others=>'0');
signal ca:std_logic_vector(7 downto 0):=(others=>'0');
signal index:natural:=0;
begin
clk<=not clk after 15 ns;clk3x<=not clk3x after 5 ns;
mem:entity tb.sdram_model3x generic map(DOREFRESH=>'1',MEMORY_BYTES=>10485760)
port map(clk=>clk,clk3x=>clk3x,refresh=>'0',addr=>addr,req=>req,ram_dma=>dma,
ram_dmacnt=>cnt,ram_iscache=>cache,rnw=>rnw,be=>be,di=>di,do=>open,do32=>dout,
done=>done,cache_wr=>cwr,cache_data=>cdata,cache_addr=>ca,dma_wr=>dma_wr,
dma_data=>ddata,reqprocessed=>ack,ram_idle=>open,ram_dmafifo_adr=>(others=>'0'),
ram_dmafifo_data=>(others=>'0'),ram_dmafifo_empty=>'1',ram_dmafifo_read=>fifo_read,
fileSize=>open,exe_initial_pc=>open,exe_initial_gp=>open,exe_load_address=>open,
exe_file_size=>open,exe_stackpointer=>open);
process
file events:text open write_mode is "oracle.txt";variable row:line;
begin
wait on clk,clk3x;
if rising_edge(clk3x) and cwr/="0000" then
write(row,index);write(row,string'(" C "));write(row,to_hstring(cwr));
write(row,string'(" "));write(row,to_hstring(ca));write(row,string'(" "));
write(row,to_hstring(cdata));writeline(events,row);
end if;
if rising_edge(clk) then
if dma_wr='1' then
write(row,index);write(row,string'(" D 0 0 "));write(row,to_hstring(ddata));writeline(events,row);
end if;
if done='1' then
write(row,index);
if rnw='1' then write(row,string'(" S 0 0 "));write(row,to_hstring(dout));
else write(row,string'(" W 0 0 00000000"));end if;
writeline(events,row);
end if;
end if;
end process;
process
file inputs:text open read_mode is "vectors.txt";variable row:line;
variable a,d:std_logic_vector(31 downto 0);variable r,c,m,n,b:std_logic_vector(3 downto 0);
variable i:natural:=0;
begin
for j in 1 to 10 loop wait until falling_edge(clk);end loop;
while not endfile(inputs) loop
readline(inputs,row);hread(row,a);hread(row,r);hread(row,c);hread(row,m);
hread(row,n);hread(row,d);hread(row,b);index<=i;
addr<=a(26 downto 0);rnw<=r(0);cache<=c(0);dma<=m(0);cnt<=n(1 downto 0);di<=d;be<=b;
wait until rising_edge(clk);req<='1';wait until rising_edge(clk);req<='0';
for j in 1 to 40 loop wait until falling_edge(clk);end loop;
i:=i+1;
end loop;
report "PASS MISTER ORACLE";finish;wait;
end process;end;
'''


def main():
    directory=ROOT/'build/sim/mister-main-adapter';directory.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=directory));rows,gold=vectors(work)
    model=prepare_memory_models(work/'upstream')[0]
    vhdl=work/'tb_oracle.vhd';vhdl.write_text(oracle_text());ghdl=find_tool('ghdl')
    commands=[
        [ghdl,'-a','--std=08','--work=tb',str(UPSTREAM/'sim/system/src/tb/globals.vhd'),model['path']],
        [ghdl,'-a','--std=08',str(vhdl)],[ghdl,'-e','--std=08','tb_mister_oracle'],
        [ghdl,'-r','--std=08','tb_mister_oracle','--max-stack-alloc=0','--assert-level=error']]
    with (work/'oracle.log').open('w') as log:
        for command in commands:subprocess.run(command,cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
    actual=[]
    for row in (work/'oracle.txt').read_text().splitlines():
        fields=row.split();actual.append((int(fields[0]),fields[1],*[int(x,16) for x in fields[2:]]))
    actual.sort(key=lambda e:(e[0],e[1]))
    if actual!=gold:
        for i,(a,b) in enumerate(zip(actual,gold)):
            if a!=b:raise ValueError(f'Upstream oracle mismatch {i}: {a} != {b}')
        raise ValueError(f'Upstream oracle event counts {len(actual)}/{len(gold)}')
    expected=work/'expected.txt'
    expected.write_text(''.join(f'{i:x} {event} {mask:x} {address:x} {data:x}\n' for i,event,mask,address,data in actual))
    controller=prepare(work/'controller')
    prefix=bench_text().split('    reg [3:0] p_valid',1)[0].replace('module tb_psx_main_memory;','module tb_psx_mister_main_adapter;')
    bench=work/'tb.sv';bench.write_text(prefix+(ROOT/'sim/memory/tb_psx_mister_main_adapter_body.sv').read_text())
    chip=work/'chip.sv';chip.write_text(chip_text().replace("seed_byte=8'(byte_addr*17+(byte_addr>>8)*23+8'h5a);",'seed_byte=0;'))
    adapter=ROOT/'rtl/pocket/psx_mister_main_adapter.sv'
    sources=[Path(controller['path']),ROOT/'references/MIRLO64/rtl/soc/psram.sv',
        ROOT/'rtl/memory/psx_mem_arb.sv',ROOT/'rtl/memory/psx_mem_cdc.sv',
        ROOT/'rtl/pocket/psx_psram_packet.sv',ROOT/'rtl/pocket/psx_main_memory.sv',adapter,
        ROOT/'sim/memory/altddio_registered_model.sv',chip,bench]
    protected={str(p):digest(p) for p in [*sources,vhdl,Path(model['path']),Path(model['original']),
        UPSTREAM/'sim/system/src/tb/globals.vhd',Path(controller['original']),
        ROOT/'sim/memory/tb_psx_mister_main_adapter_body.sv',ROOT/'sim/memory/tb_psx_psram_burst.sv',
        ROOT/'sim/memory/cellularram_burst_model.sv',ROOT/'scripts/prepare_psram_burst.py',
        ROOT/'scripts/run_main_memory.py',ROOT/'scripts/prepare_system_sim.py',Path(__file__),work/'vectors.txt',expected]}
    for i,p in enumerate(protected):
        target=work/'inputs'/str(i)/Path(p).name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
    runs=[]
    def run(name,parameters=(),mutation=None,marker=None):
        d=work/name;d.mkdir();binary=d/'sim.vvp';selected=sources
        if mutation:
            before,after=mutation;text=adapter.read_text()
            if text.count(before)!=1:raise ValueError('Adapter fault anchor changed')
            copy=d/'adapter.sv';copy.write_text(text.replace(before,after));selected=[copy if p==adapter else p for p in sources]
        with (d/'build.log').open('w') as log:
            subprocess.run([find_tool('iverilog'),'-g2012','-s','tb_psx_mister_main_adapter',
                *[f'-Ptb_psx_mister_main_adapter.{k}={v}' for k,v in parameters],'-o',str(binary),
                *map(str,selected)],cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
        r=subprocess.run([find_tool('vvp'),str(binary)],cwd=work,capture_output=True,text=True,timeout=180)
        output=r.stdout+r.stderr;(d/'run.log').write_text(output)
        passed=r.returncode!=0 and marker in output if marker else r.returncode==0 and 'FATAL' not in output and output.count('PASS MISTER MAIN ADAPTER')==1
        runs.append(dict(name=name,parameters=dict(parameters),passed=passed,exit_code=r.returncode,
            output=output,binary_sha256=digest(binary),fault_marker=marker))
        print(name,passed,output,flush=True);return passed
    ok=run('nominal')
    if ok:
        run('delayed',[('CLOCK_DELAY',4),('CONTROL_DELAY',3),('RETURN_DELAY',.5)])
        run('fast-chip',[('DATA_ACCESS',5)])
        run('psx-clock',[('CORE_HALF',14.763)])
        run('cache-mask',mutation=("cache_mask<=4'b0001<<request_payload[39:38]", "cache_mask<=4'b0001"),marker='Native cache event mismatch')
        run('row-wrap',mutation=("byte_address<={byte_address[24:10],10'b0}","byte_address<=byte_address+{19'b0,packet_words,2'b0}"),marker='Native return event mismatch')
        run('dma-order',mutation=('assign dma_data=return_data[return_index*32+:32]','assign dma_data=return_data[(return_count-return_index)*32+:32]'),marker='Native return event mismatch')
        run('lost-ack',mutation=('if(core_ce || discard_returns) dma_ack_pending<=0','dma_ack_pending<=0'),marker='DMA ACK lost across CE pause')
        run('lost-mask',mutation=(".s_byte_en({12'b0,write_mask})",".s_byte_en(16'h000f)"),marker='Native return event mismatch')
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    report=dict(passed=identity and len(runs)==9 and all(r['passed'] for r in runs),
        source_identity_verified=identity,protected_sha256=protected,runs=runs,
        upstream_model=model,vector_count=len(rows),oracle_events=len(gold),
        physical_controller=controller,CPU_execution_verified=False,hardware_verified=False,
        limitations=['Port-level authored traffic, not actual CPU/cache/DMA execution',
            'Independent fixed-latency pin models; board electrical/variable WAIT not proved',
            'Common reset; source/response mailboxes need physical bundled-data constraints',
            'Memory completion/cache readiness latency differs from stock SDRAM; game deadlines unproved'])
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
