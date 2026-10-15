#!/usr/bin/env python3
"""Check SOTN FIFO RAM with actual Intel models, mux ports and CPU stores.

Private copies only. Requires the local Quartus simulation library; nothing
from that proprietary library is copied into Git. Compile errors do not count
as rejection of a planted behavioral fault.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from cpu_cache_program import write_program
from prepare_memorymux_m10k import SOTN, SOTN_PIN, cpu_text, mux_text, reference_sources
from prepare_no_turbo_cache import no_turbo_cache_source
from prepare_resource_targets import UPSTREAM, digest, ports
from run_cpu_cache_contract import observed_cpu, paired_bench
from tool_paths import ROOT, find_tool


def mux_bench():
    declarations, instances = [], []
    control = dict(clk1x='clk', clk2x='clk2x', reset='reset', ce='ce', pauseNext='pause',
                   mem_in_request='request', mem_in_rnw='rnw', mem_in_isData="'1'",
                   mem_in_addressData='addr', mem_in_writeMask='mask', mem_in_reqsize='size',
                   mem_in_dataWrite='data', ram_done='ram_done', ram_dataRead='ram_data')
    outputs = []
    for library, prefix in (('baseline', 'a_'), ('candidate', 'b_')):
        associations = []
        for name, direction, kind in ports('memorymux'):
            if direction == 'out':
                declarations.append(f' signal {prefix+name}: {kind};')
                associations.append(f'{name}=>{prefix+name}')
                if prefix == 'a_': outputs.append(name)
            else:
                default = "'0'" if kind == 'std_logic' else "(others=>'0')"
                associations.append(f'{name}=>{control.get(name, default)}')
        if library == 'candidate': associations.append('mem_in_writeRequest=>request and not rnw')
        instances.append(f' {prefix}mux: entity {library}.memorymux port map ('+', '.join(associations)+');')
    enables = [n for n in outputs if n.endswith(('_read', '_write')) or n in
               ('ram_ena', 'mem_done', 'mem_fifofull', 'isIdle', 'SS_idle', 'reset_exe')]
    compare = '\n'.join(f'  assert a_{n}=b_{n} report "Mux control mismatch: {n}" severity failure;' for n in enables)
    compare += '''
  if a_ram_ena='1' then
   assert a_ram_Adr=b_ram_Adr and a_ram_be=b_ram_be and a_ram_rnw=b_ram_rnw
     and a_ram_cache=b_ram_cache report "Mux RAM control mismatch" severity failure;
   if a_ram_rnw='0' then
    assert a_ram_dataWrite=b_ram_dataWrite report "Mux RAM store mismatch" severity failure;
   end if;
  end if;
  if a_mem_done='1' then
   assert a_mem_dataRead=b_mem_dataRead report "Mux read return mismatch" severity failure;
   assert a_mem_tagvalids=b_mem_tagvalids report "Mux tag-valid mismatch" severity failure;
  end if;
'''
    # Active peripheral payload ports, even though this first traffic fixture
    # primarily stresses RAM writes/reads. Never compare unused FIFO payloads.
    for n in outputs:
        m = re.match(r'(bus_\w+)_(addr|dataWrite|writeMask)$', n)
        if not m: continue
        signals = [f'a_{m[1]}_{s}' for s in ('read', 'write') if m[1]+'_'+s in outputs]
        if signals:
            condition = ' or '.join(s+"='1'" for s in signals)
            compare += f'  if {condition} then assert a_{n}=b_{n} report "Mux peripheral payload mismatch: {n}" severity failure; end if;\n'
    return '''library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use ieee.math_real.all;
use std.env.all;
library baseline, candidate;
entity tb_mux_m10k is
 generic(SEED: positive:=1; CYCLES:positive:=120000);
end;
architecture test of tb_mux_m10k is
 signal clk,clk2x:std_logic:='1';
 signal reset:std_logic:='1';
 signal ce:std_logic:='1';
 signal pause,request,ram_done:std_logic:='0';
 signal rnw:std_logic:='1';
 signal addr:unsigned(31 downto 0):=(others=>'0');
 signal data,ram_data:std_logic_vector(31 downto 0):=(others=>'0');
 signal mask:std_logic_vector(3 downto 0):="1111";
 signal size:unsigned(1 downto 0):="10";
'''+'\n'.join(declarations)+'''
begin
 clk<=not clk after 15 ns;
 clk2x<=not clk2x after 7500 ps;
'''+'\n'.join(instances)+'''
 process
  type word_list is array(0 to CYCLES+8) of std_logic_vector(31 downto 0);
  type mask_list is array(0 to CYCLES+8) of std_logic_vector(3 downto 0);
  variable values,addresses:word_list;
  variable masks:mask_list;
  variable wr,rd,issued,committed,aborted,reads,nearfulls,pauses,resets:natural:=0;
  variable delay:natural:=0;
  variable pending:boolean:=false;
  variable reading:boolean:=false;
  variable old_reset:std_logic:='1';
  variable s1:positive:=SEED;
  variable s2:positive:=SEED*7+3;
  variable r:real;
 begin
  for tick in 0 to CYCLES+300 loop
   wait until rising_edge(clk);
   if reset='0' then
'''+compare+'''
    if ce='1' and request='1' and rnw='0' then
     values(wr):=data; addresses(wr):=std_logic_vector(addr); masks(wr):=mask;
     wr:=wr+1; issued:=issued+1;
    end if;
    if a_ram_ena='1' and not pending then
     pending:=true; delay:=1+(tick+SEED) mod 11;
     if a_ram_rnw='0' then
      assert rd<wr report "Mux unexpected store" severity failure;
      assert a_ram_dataWrite=values(rd) and a_ram_Adr=addresses(rd)(24 downto 0)
        and a_ram_be=masks(rd) report "Mux store order/payload mismatch" severity failure;
      rd:=rd+1;committed:=committed+1;
     else reads:=reads+1;
     end if;
    end if;
    if reading and a_mem_done='1' then reading:=false; end if;
   else
    if old_reset='0' then aborted:=aborted+wr-rd;resets:=resets+1;end if;
    wr:=0;rd:=0;reading:=false;
   end if;
   old_reset:=reset;
   wait for 1 ns; -- all client controls launch from the 1x rising edge
   ram_done<='0';
   if pending then
    if delay=0 then
     ram_done<='1';ram_data<=std_logic_vector(to_unsigned(tick*173,32));pending:=false;
    else delay:=delay-1;
    end if;
   end if;
   request<='0';pause<='0';ce<='1';reset<='0';
   if tick<4 then reset<='1';
   elsif tick<CYCLES then
    uniform(s1,s2,r);
    if tick mod 1000=990 and not pending then reset<='1'; end if;
    if tick mod 97<12 then pause<='1';pauses:=pauses+1;end if;
    -- Like the core's pause contract: stop CE only when the mux is idle.
    if a_isIdle='1' and not pending and not reading and r<0.08 then ce<='0';
    elsif a_mem_fifofull='0' and not reading and tick mod 1000<900 then
     uniform(s1,s2,r);
     if tick mod 1000<600 and r<0.75 then
      request<='1';rnw<='0';addr<=to_unsigned((issued*4) mod 131072,32);
      data<=std_logic_vector(to_unsigned(issued*5323+SEED,32));
      mask<=std_logic_vector(to_unsigned(1+issued mod 15,4));
      size<=to_unsigned(issued mod 3,2);
     elsif r<0.85 and a_isIdle='1' and wr=rd and not pending then
      request<='1';rnw<='1';addr<=to_unsigned((tick*4) mod 131072,32);reading:=true;
     end if;
    end if;
    if a_mem_fifofull='1' then nearfulls:=nearfulls+1;end if;
   end if;
  end loop;
  assert wr=rd and issued=committed+aborted report "Mux undrained stores" severity failure;
  report "PASS Mux M10K issued="&natural'image(issued)&" committed="&natural'image(committed)&
   " aborted="&natural'image(aborted)&" reads="&natural'image(reads)&" nearfull="&natural'image(nearfulls)&
   " resets="&natural'image(resets);
  assert committed>1000 and reads>20 and nearfulls>100 and pauses>100 and resets>0
    report "Mux coverage missing" severity failure;
  stop;wait;
 end process;
end;
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selected-cpu', type=Path, help='Also support the actual captured CPU (e.g. falling-edge tags) as both oracle inputs')
    parser.add_argument('--selected-mux', type=Path, help='Captured memorymux to compare against its transformed copy')
    args = parser.parse_args()
    ghdl, quartus = find_tool('ghdl'), find_tool('quartus_sh')
    if not ghdl or not quartus:
        raise SystemExit('GHDL and a local Quartus installation are required')
    simlib = Path(quartus).resolve().parents[1]/'eda/sim_lib'
    parent = ROOT/'build/sim/memorymux-m10k'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent)); print(work, flush=True)
    report = dict(reference_pin=SOTN_PIN, runs=[], faults=[], passed=False,
                  game_verified=False, hardware_verified=False)
    protected = [Path(__file__), ROOT/'scripts/prepare_memorymux_m10k.py',
                 ROOT/'sim/reference/tb_cpu_cache_contract.vhd',
                 *(UPSTREAM/'rtl'/n for n in ('cpu.vhd','memorymux.vhd','SyncFifoFallThroughMLAB.vhd'))]
    protected += [simlib/'altera_mf_components.vhd', simlib/'altera_mf.vhd']
    protected += [SOTN/'rtl/RamM10Kn.vhd', SOTN/'rtl/SyncFifoFallThroughMLAB.vhd',
                  SOTN/'sim/fifo_m10k/tb_fifo_m10k.vhd']
    protected += [p.resolve() for p in (args.selected_cpu, args.selected_mux) if p]
    identities = {str(p):digest(p) for p in protected}
    report['source_sha256'] = identities
    report['vendor_models'] = str(simlib)
    report['ghdl_version'] = subprocess.check_output([ghdl,'--version'],text=True).splitlines()[0]
    flags = ['--std=08', '-frelaxed', '-fsynopsys']

    def command(args, cwd=work):
        r = subprocess.run([ghdl, *args], cwd=cwd, capture_output=True, text=True, timeout=180)
        with (work/'compile.log').open('a') as f: f.write(' '.join(args)+'\n'+r.stdout+r.stderr)
        if r.returncode: raise RuntimeError('Compilation failed, see '+str(work/'compile.log'))

    def analyze(library, path, cwd=work): command(['-a', *flags, '--work='+library, str(path)], cwd)

    def run(entity, name, generics, marker, fault=None, cwd=work):
        command(['-e', *flags, entity], cwd)
        r = subprocess.run([ghdl, '-r', *flags, entity, *generics, '--assert-level=error',
                            '--ieee-asserts=disable-at-0'], cwd=cwd, capture_output=True, text=True, timeout=180)
        log = work/(name+'.log'); log.write_text(r.stdout+r.stderr)
        passed = r.returncode==0 and marker in r.stdout
        if fault: passed = r.returncode!=0 and fault in r.stdout+r.stderr
        row = dict(name=name, passed=passed, exit_code=r.returncode, log=str(log),
                   log_sha256=digest(log), output=(r.stdout+r.stderr)[-2000:])
        report['faults' if fault else 'runs'].append(row); print(name, passed, row['output'][-700:], flush=True)
        if not passed: raise RuntimeError('Check failed: '+name)

    try:
        for n in ('altera_mf_components.vhd', 'altera_mf.vhd'): analyze('altera_mf', simlib/n)
        reference_sources(work/'imported')
        for n in ('RamMLAB.vhd',): analyze('mem', UPSTREAM/'rtl'/n)
        ram = work/'imported/RamM10Kn.vhd'
        analyze('mem', ram)
        analyze('mem', work/'imported/SyncFifoFallThroughMLAB.vhd')
        fifo_bench = work/'tb_fifo_m10k.vhd'
        # Identical FIFO control body in the pinned MiSTer and SOTN sources.
        base = (UPSTREAM/'rtl/SyncFifoFallThroughMLAB.vhd').read_text()
        adapted = (SOTN/'rtl/SyncFifoFallThroughMLAB.vhd').read_text()
        assert base[base.index('   full_wire      <='):]==adapted[adapted.index('   full_wire      <='):]
        # A synchronous reset consumes no queue data on that edge.
        fifo_bench.write_text((SOTN/'sim/fifo_m10k/tb_fifo_m10k.vhd').read_text().replace(
            "(a_Empty = '0' and a_Dout /= b_Dout)",
            "(reset = '0' and a_Empty = '0' and a_Dout /= b_Dout)"))
        analyze('work', fifo_bench)
        for seed in (1,2,3):
            run('tb_fifo_m10k', 'fifo-seed'+str(seed), ['-gNUM_CYCLES=200000','-gSEED='+str(seed)], 'errors=0\nPASS')
        # This upstream bench reports FAIL instead of raising: use a monitor
        # assertion in the fault copy so a clean simulation exit is not enough.
        text = fifo_bench.read_text().replace('      done <= true;',
            '      assert errors=0 report "FIFO phase mismatch" severity failure;\n      done <= true;')
        fifo_bench.write_text(text)
        badram = work/'rising-RamM10Kn.vhd';badram.write_text(ram.read_text().replace('clk_n <= not inclock;', 'clk_n <= inclock;'))
        analyze('mem', badram); analyze('mem', work/'imported/SyncFifoFallThroughMLAB.vhd'); analyze('work', fifo_bench)
        run('tb_fifo_m10k','fault-fifo-rising',['-gNUM_CYCLES=10000'],'',fault='FIFO phase mismatch')
        wrongaddr = work/'wrong-address-RamM10Kn.vhd'
        wrongaddr.write_text(ram.read_text().replace('address_b => rdaddress,', 'address_b => not rdaddress,'))
        assert wrongaddr.read_text()!=ram.read_text()
        analyze('mem',wrongaddr);analyze('mem',work/'imported/SyncFifoFallThroughMLAB.vhd');analyze('work',fifo_bench)
        run('tb_fifo_m10k','fault-fifo-address',['-gNUM_CYCLES=10000'],'',fault='FIFO phase mismatch')
        analyze('mem', ram); analyze('mem', work/'imported/SyncFifoFallThroughMLAB.vhd')
        selected_mux = (args.selected_mux or UPSTREAM/'rtl/memorymux.vhd').resolve()
        analyze('baseline', selected_mux)
        mux = work/'memorymux.vhd'; mux.write_text(mux_text(selected_mux.read_text()))
        analyze('candidate', mux)
        tb = work/'tb_mux_m10k.vhd';tb.write_text(mux_bench());analyze('work', tb)
        for seed in (1,7,19):run('tb_mux_m10k','mux-seed'+str(seed),['-gSEED='+str(seed)],'PASS Mux M10K')
        badmux=work/'bad-memorymux.vhd';badmux.write_text(mux.read_text().replace("ce = '1' and mem_in_writeRequest = '1'", "ce = '1' and mem_in_writeRequest = '0'"))
        analyze('candidate',badmux);analyze('work',tb)
        run('tb_mux_m10k','fault-mux-store',[],'',fault='Mux control mismatch:')
        # Real CPU, authored cached program and existing retirement/GPR/store oracle.
        cpu = work/'cpu-check';cpu.mkdir(); program=write_program(cpu)
        for lib in ('work','candidate'):
            analyze('mem',UPSTREAM/'sim/system/src/mem/RamMLAB.vhd',cpu)
            analyze(lib,UPSTREAM/'sim/system/src/mem/dpram.vhd',cpu)
            for n in ('export.vhd','divider.vhd','datacache.vhd'):analyze(lib,UPSTREAM/'rtl'/n,cpu)
        selected_cpu = (args.selected_cpu or UPSTREAM/'rtl/cpu.vhd').resolve()
        original=cpu/'original.vhd';original.write_text(observed_cpu(selected_cpu.read_text()));analyze('work',original,cpu)
        lean = selected_cpu if args.selected_cpu else Path(no_turbo_cache_source(cpu/'lean')['path'])
        candidate=cpu/'candidate.vhd';candidate.write_text(cpu_text(observed_cpu(lean.read_text())));analyze('candidate',candidate,cpu)
        report['selected_cpu'] = dict(path=str(selected_cpu),sha256=digest(selected_cpu))
        report['selected_mux'] = dict(path=str(selected_mux),sha256=digest(selected_mux))
        text=paired_bench().replace('    -- GENERATED_DECLARATIONS', '')
        text=text.replace('    signal clk1x,clk2x,clk3x', '    signal short_write:std_logic;\n    signal clk1x,clk2x,clk3x')
        text=text.replace('    candidate_cpu : entity candidate.cpu port map (',
                          '    candidate_cpu : entity candidate.cpu port map (\n        mem_writeRequest=>short_write,')
        assert 'mem_writeRequest=>short_write' in text
        text=text.replace('    process\n        variable enabled : boolean;', '''    process(clk1x)
    begin
        if clk1x'event and reset='0' then
            assert short_write=(candidate_request and not candidate_rnw)
                report "CPU short write request mismatch" severity failure;
        end if;
    end process;
    process
        variable enabled : boolean;''')
        tb=cpu/'tb_cpu_cache_contract.vhd';tb.write_text(text);analyze('work',tb,cpu)
        for seed,pauses,early,resets in ((1,False,False,False),(19,True,True,False),(31,True,True,True)):
            gs=[f'-gSEED={seed}',f'-gEXPECTED_COUNT={program["expected_checks"]}',f'-gPAUSES={str(pauses).lower()}',
                f'-gEARLY_DONE={str(early).lower()}',f'-gRESETS={str(resets).lower()}']
            run('tb_cpu_cache_contract','cpu-seed'+str(seed),gs,'PASS CPU cache',cwd=cpu)
        bad=cpu/'bad-cpu.vhd';bad.write_text(candidate.read_text().replace("mem4_rnw = '0'\n", "mem4_rnw = '1'\n"))
        analyze('candidate',bad,cpu);analyze('work',tb,cpu)
        run('tb_cpu_cache_contract','fault-short-request',[f'-gEXPECTED_COUNT={program["expected_checks"]}'],'',
            fault='CPU short write request mismatch',cwd=cpu)
        report['source_sha256']=identities
        report['source_identity_verified']=all(digest(Path(p))==h for p,h in identities.items())
        report['generated_sources']={str(p.relative_to(work)):digest(p) for p in work.rglob('*.vhd')}
        report['passed']=report['source_identity_verified'] and all(r['passed'] for r in report['runs']+report['faults'])
    finally:
        (work/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print('report='+str(work/'report.json'),flush=True)


if __name__=='__main__':main()
