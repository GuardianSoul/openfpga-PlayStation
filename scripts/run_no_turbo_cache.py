#!/usr/bin/env python3
"""Compare original CPU and no-turbo-cache CPU on bus, GTE and retired state."""
import json
import re
import subprocess

from prepare_no_turbo_cache import no_turbo_cache_source
from prepare_resource_targets import UPSTREAM, digest, ports
from reference_cpu_gte_program import write_program
from tool_paths import ROOT, find_tool


def paired_bench(directory, checks):
    original=ROOT/'sim/reference/tb_mister_cpu_gte.vhd'; text=original.read_text()
    text=text.replace('entity tb_mister_cpu_gte is','entity tb_no_turbo_cache is').replace(
        'architecture test of tb_mister_cpu_gte is','architecture test of tb_no_turbo_cache is')
    text=text.replace('use work.pexport.all;','use work.pexport.all;\nlibrary lean;')
    # The reset program in this folder adds independently expected scratchpad
    # byte/halfword/word probes; normal CPU/GTE diagnostics remain unchanged.
    text=text.replace('words(0 to 2643) := load(EXPECTED_FILE,2644)',
                      f'words(0 to {checks-1}) := load(EXPECTED_FILE,{checks})')
    instance=re.search(r'    cpu : entity work.cpu port map \(\n.*?\n    \);',text,re.S)
    if not instance: raise ValueError('Original CPU diagnostic instance changed')
    reference=instance[0]; candidate=reference.replace('cpu : entity work.cpu','lean_cpu : entity lean.cpu')
    declarations=[]; outputs={}; original_outputs={}
    for name,direction,kind in ports('cpu'):
        if direction!='out': continue
        value=re.search(r'\b'+name+r'=>(\w+)',reference)
        if not value: raise ValueError('CPU diagnostic output mapping changed: '+name)
        baseline=value[1]
        if baseline=='open':
            baseline='reference_'+name
            reference=re.sub(r'\b'+name+r'=>open',name+'=>'+baseline,reference)
            declarations.append(f'    signal {baseline} : {kind};\n')
        replacement='lean_'+name
        candidate=re.sub(r'\b'+name+r'=>\w+',name+'=>'+replacement,candidate)
        declarations.append(f'    signal {replacement} : {kind};\n')
        original_outputs[name]=baseline; outputs[name]=replacement
    candidate=candidate.replace('cpu_done=>cpu_done','cpu_done=>lean_done').replace('cpu_export=>cpu_export','cpu_export=>lean_export')
    declarations+=['    signal lean_done : std_logic;\n','    signal lean_export : lean.pexport.cpu_export_type;\n']
    text=text.replace('    signal retired,commands : natural := 0;\nbegin',
                      '    signal retired,commands : natural := 0;\n'+''.join(declarations)+'begin')
    # Locations changed by inserted declarations: find the complete original
    # instance text, then replace it rather than reuse stale string offsets.
    text=text.replace(instance[0],reference+'\n'+candidate)
    observer='''    process
    begin
        wait until falling_edge(clk3x); wait for 1 ps;
        if reset='0' then
'''
    for name in ('mem_request','stallNext','error','error2','gte_readEna','gte_writeEna','gte_cmdEna','SS_idle'):
        observer+=f'            assert {original_outputs[name]}={outputs[name]} report "CPU control mismatch: {name}" severity failure;\n'
    observer+='''            assert cpu_done=lean_done report "CPU retirement timing mismatch" severity failure;
            if request='1' then
'''
    for name in ('mem_rnw','mem_isData','mem_isCache','mem_reqsize'):
        observer+=f'                assert {original_outputs[name]}={outputs[name]} report "CPU memory control mismatch: {name}" severity failure;\n'
    observer+='''                if isdata='1' then
                    assert addr_data=lean_mem_addressData report "CPU data address mismatch" severity failure;
                    if rnw='0' then
                        assert write_data=lean_mem_dataWrite and mask=lean_mem_writeMask
                            report "CPU store payload mismatch" severity failure;
                    end if;
                else
                    assert addr_instr=lean_mem_addressInstr and oldtags=lean_mem_oldtagvalids
                        report "CPU fetch address/tag mismatch" severity failure;
                end if;
            end if;
            if gte_read_ena='1' then
                assert gte_read_addr=lean_gte_readAddr report "CPU GTE read address mismatch" severity failure;
            end if;
            if gte_write_ena='1' then
                assert gte_write_addr=lean_gte_writeAddr and gte_write_data=lean_gte_writeData
                    report "CPU GTE write payload mismatch" severity failure;
            end if;
            if gte_cmd_ena='1' then
                assert gte_cmd_data=lean_gte_cmdData report "CPU GTE command mismatch" severity failure;
            end if;
            if cpu_done='1' then
                assert cpu_export.pc=lean_export.pc and cpu_export.opcode=lean_export.opcode and cpu_export.cause=lean_export.cause
                    report "CPU retired instruction/state mismatch" severity failure;
                for reg in 0 to 31 loop
                    assert cpu_export.regs(reg)=lean_export.regs(reg)
                        report "CPU GPR mismatch: " & integer'image(reg) severity failure;
                end loop;
            end if;
        end if;
    end process;
'''
    text=text.replace('    process begin wait for 100 ms;',observer+'    process begin wait for 100 ms;')
    path=directory/'tb_no_turbo_cache.vhd'; path.write_text(text)
    return path


def main():
    ghdl=find_tool('ghdl')
    if not ghdl: raise SystemExit('GHDL is required for CPU cache specialization checks')
    directory=ROOT/'build/sim/no-turbo-cache'; directory.mkdir(parents=True,exist_ok=True)
    variant=no_turbo_cache_source(directory/'adapted')
    program=write_program(directory/'cpu-gte',memory_checks=True)
    bench=paired_bench(directory,program['expected_checks']); log=directory/'checks.log'
    files=[('mem',UPSTREAM/'sim/system/src/mem/RamMLAB.vhd'),('work',UPSTREAM/'sim/system/src/mem/dpram.vhd'),
        ('work',UPSTREAM/'rtl/export.vhd'),('work',UPSTREAM/'rtl/divider.vhd'),('work',UPSTREAM/'rtl/datacache.vhd'),
        ('work',UPSTREAM/'rtl/cpu.vhd'),('lean',UPSTREAM/'sim/system/src/mem/dpram.vhd'),
        ('lean',UPSTREAM/'rtl/export.vhd'),('lean',UPSTREAM/'rtl/divider.vhd'),('lean',variant['path'])]
    files += [('work',UPSTREAM/'rtl'/name) for name in ('pGTE.vhd','gte_mac0.vhd','gte_mac123.vhd','gte_UNRDivide.vhd','gte.vhd')]
    files.append(('work',bench))
    runs=[]
    with log.open('w') as output:
        def checked(command,timeout=120):
            result=subprocess.run(command,cwd=directory,capture_output=True,text=True,timeout=timeout)
            output.write(result.stdout+result.stderr); output.flush()
            if result.returncode: raise SystemExit(result.stdout+result.stderr)
            return result.stdout+result.stderr
        for library,path in files:
            checked([ghdl,'-a','--std=08','-frelaxed',f'--work={library}',str(path)])
        checked([ghdl,'-e','--std=08','-frelaxed','tb_no_turbo_cache'])
        for seed in (1,7,19):
            stdout=checked([ghdl,'-r','--std=08','-frelaxed','tb_no_turbo_cache',f'-gSEED={seed}',
                '--assert-level=error','--ieee-asserts=disable-at-0'])
            passed=f'commands=448 checks={program["expected_checks"]}' in stdout and 'PASS MiSTer CPU/GTE' in stdout
            print(stdout,end='',flush=True); runs.append({'seed':seed,'passed':passed,'stdout':stdout})
    (directory/'probe-report.json').write_text(json.dumps({'source':variant,'bench_sha256':digest(bench),
        'ghdl_version':subprocess.check_output([ghdl,'--version'],text=True).splitlines()[0],
        'bench_generator_sha256':digest(ROOT/'scripts/run_no_turbo_cache.py'),
        'program_sha256':digest(program['program']),'expected_sha256':digest(program['expected']),
        'instructions_in_program':program['instructions'],'gte_commands':program['gte_commands'],
        'mathematical_checks':program['mathematical_checks'],'scratchpad_checks':program['scratchpad_checks'],
        'compared':'CPU control and active bus/GTE transfers every 3x falling edge; full GPR/PC/opcode/cause at retirement',
        'runs':runs,'passed':all(r['passed'] for r in runs),'cached_code_execution_verified':False,
        'dma_snoop_tested':False,'bios_boot_verified':False,'game_verified':False,'hardware_verified':False},indent=2)+'\n')
    if not all(r['passed'] for r in runs): raise SystemExit('CPU specialization comparison did not complete')


if __name__=='__main__': main()
