#!/usr/bin/env python3
"""Cached CPU oracle/lockstep and an unselected falling-edge tag RAM trial."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

from cpu_cache_program import write_program
from prepare_no_turbo_cache import no_turbo_cache_source
from prepare_resource_targets import UPSTREAM, PIN, digest, ports
from tool_paths import ROOT, find_tool


def observed_cpu(text):
    """Read-only simulation ports; no core state, consumer or clock changes."""
    marker = '      debug_firstGTE        : in  std_logic'
    if text.count(marker) != 1:
        raise ValueError('CPU debug port marker changed')
    extra = '''      -- synthesis translate_off
      probe_cop0 : out std_logic_vector(95 downto 0);
      probe_fetch : out unsigned(31 downto 0);
      probe_tag : out std_logic_vector(23 downto 0);
      probe_accept : out std_logic;
      probe_invalidate : out std_logic;
      -- synthesis translate_on
'''
    text = text.replace(marker, extra+marker)
    marker = '   -- IO'
    if text.count(marker) != 1:
        raise ValueError('CPU architecture marker changed')
    return text.replace(marker, '''   -- synthesis translate_off
   probe_cop0 <= std_logic_vector(cop0_SR) & std_logic_vector(cop0_CAUSE) & std_logic_vector(cop0_EPC);
   probe_fetch <= FetchAddr;
   probe_tag <= tag_q_b;
   probe_accept <= '1' when reset='0' and ce='1' and stall=0 else '0';
   probe_invalidate <= writebackInvalidateCacheEna;
   -- synthesis translate_on

'''+marker)


def tag_trial(text, mode):
    if mode == 'original':
        return text
    pattern = r'   itagram : entity mem.RamMLAB\n.*?\n   \);'
    match = re.search(pattern, text, re.S)
    if not match or 'width      => 24' not in match[0] or 'widthad    => 8' not in match[0]:
        raise ValueError('CPU tag instance changed')
    if mode == 'wrong-address':
        replacement = match[0].replace('rdaddress  => tag_address_b',
                                        'rdaddress  => tag_address_b xor x"01"')
    elif mode in ('falling', 'rising', 'falling-address-model'):
        replacement = '''   -- UNSELECTED experiment: the upstream write edge is retained.
   itagram : entity work.dpram
   generic map (addr_width => 8, data_width => 24)
   port map (
      clock_a => clk1x, address_a => tag_address_a,
      data_a => tag_data_a, wren_a => tag_wren_a, q_a => open,
      clock_b => READ_CLOCK, address_b => tag_address_b,
      data_b => (others=>'0'), wren_b => '0', q_b => tag_q_b
   );'''.replace('READ_CLOCK', 'clk1x' if mode == 'rising' else 'not clk1x')
        if mode == 'falling-address-model':
            replacement = replacement.replace('entity work.dpram','entity work.cpu_tag_phase_model')
    else:
        raise ValueError(mode)
    return text[:match.start()] + replacement + text[match.end():]


def paired_bench():
    text = (ROOT/'sim/reference/tb_cpu_cache_contract.vhd').read_text()
    inputs = {
        'clk1x':'clk1x', 'clk2x':'clk2x', 'clk3x':'clk3x', 'ce':'ce', 'reset':'reset',
        'TURBO':"'0'", 'TURBO_CACHE':"'0'", 'TURBO_CACHE50':"'0'", 'irqRequest':'irq',
        'dmaStallCPU':'dma_stall', 'cpuPaused':'dma_stall', 'mem_dataRead':'read_data',
        'mem_done':'done', 'mem_fifofull':"'0'", 'mem_tagvalids':'tagvalids',
        'cache_wr':'cache_wr', 'cache_data':'cache_data', 'cache_addr':'cache_addr',
        'dma_cache_Adr':'dma_cache_addr', 'dma_cache_data':'dma_cache_data',
        'dma_cache_write':'dma_cache_write', 'ram_done':'done', 'ram_rnw':'ram_rnw',
        'ram_dataRead':'ram_data', 'gte_busy':"'0'", 'gte_readData':"(others=>'0')",
        'SS_reset':'ss_reset', 'SS_DataWrite':"(others=>'0')", 'SS_Adr':"(others=>'0')",
        'SS_wren_CPU':"'0'", 'SS_wren_SCP':"'0'", 'SS_rden_CPU':"'0'", 'SS_rden_SCP':"'0'",
        'debug_firstGTE':"'0'",
    }
    aliases = {'mem_request':'request', 'mem_rnw':'rnw', 'mem_isData':'isdata',
               'mem_isCache':'iscache', 'mem_oldtagvalids':'oldtags', 'mem_addressInstr':'addr_instr',
               'mem_addressData':'addr_data', 'mem_reqsize':'reqsize', 'mem_writeMask':'mask',
               'mem_dataWrite':'write_data'}
    definitions = ports('cpu') + [
        ('cpu_done','out','std_logic'), ('cpu_export','out','cpu_export_type')]
    declarations, instances, outputs = [], [], {}
    for library, prefix in (('work',''), ('candidate','candidate_')):
        associations = []
        for name, direction, kind in definitions:
            if direction == 'out':
                signal = prefix+aliases.get(name,name)
                if kind == 'cpu_export_type' and library == 'candidate':
                    kind = 'candidate.pexport.cpu_export_type'
                declarations.append(f'    signal {signal} : {kind};')
                associations.append(f'{name}=>{signal}')
                if not prefix:
                    outputs[name] = signal
            else:
                associations.append(f'{name}=>{inputs[name]}')
        for name in ('probe_cop0','probe_fetch','probe_tag','probe_accept','probe_invalidate'):
            associations.append(f'{name}=>{prefix+name}')
        instances.append(f'    {library}_cpu : entity {library}.cpu port map (\n        '+
                         ',\n        '.join(associations)+'\n    );')
    observer = '''    process
        variable enabled : boolean;
    begin
        wait until rising_edge(clk1x);
        enabled:=ce='1';
        if reset='0' and ce='1' then
            checked_edges<=checked_edges+1;
'''
    for name in ('mem_request', 'stallNext', 'error', 'error2', 'SS_idle',
                 'gte_readEna', 'gte_writeEna', 'gte_cmdEna'):
        signal = outputs[name]
        observer += f'            assert {signal}=candidate_{signal} report "CPU consumer control mismatch: {name}" severity failure;\n'
    observer += '''            if request='1' then
'''
    for name in ('mem_rnw','mem_isData','mem_isCache','mem_reqsize'):
        signal = outputs[name]
        observer += f'                assert {signal}=candidate_{signal} report "CPU memory control mismatch: {name}" severity failure;\n'
    observer += '''                if isdata='1' then
                    assert addr_data=candidate_addr_data report "CPU data address mismatch" severity failure;
                    if rnw='0' then
                        assert write_data=candidate_write_data and mask=candidate_mask
                            report "CPU store payload mismatch" severity failure;
                    end if;
                else
                    assert addr_instr=candidate_addr_instr and oldtags=candidate_oldtags
                        report "CPU fetch address/valid mask mismatch" severity failure;
                end if;
            end if;
        end if;
        wait for 1 ps;
        if reset='0' then
            assert probe_cop0=candidate_probe_cop0 report "COP0 SR/Cause/EPC mismatch" severity failure;
            assert cpu_done=candidate_cpu_done report "CPU retirement timing mismatch" severity failure;
            if cpu_done='1' and enabled then
                retired<=retired+1;
                if cpu_export.pc(31 downto 29)="000" or cpu_export.pc(31 downto 29)="100" then
                    cached_retired<=cached_retired+1;
                end if;
                assert cpu_export.pc=candidate_cpu_export.pc and cpu_export.opcode=candidate_cpu_export.opcode
                    and cpu_export.cause=candidate_cpu_export.cause report "CPU retired PC/opcode/Cause mismatch" severity failure;
                for reg in 0 to 31 loop
                    assert cpu_export.regs(reg)=candidate_cpu_export.regs(reg)
                        report "CPU retired GPR mismatch: " & integer'image(reg) severity failure;
                end loop;
                if TRACE then report "CPU pc=" & to_hstring(cpu_export.pc) & " op=" & to_hstring(cpu_export.opcode); end if;
            end if;
        end if;
    end process;
'''
    return text.replace('    -- GENERATED_DECLARATIONS','\n'.join(declarations)).replace(
        '    -- GENERATED_INSTANCES','\n'.join(instances)).replace('    -- GENERATED_OBSERVER',observer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true', help='one diagnostic run, no injected faults')
    parser.add_argument('--trace', action='store_true')
    args = parser.parse_args()
    ghdl = find_tool('ghdl')
    if not ghdl:
        raise SystemExit('GHDL required')
    parent = ROOT/'build/sim/cpu-cache-contract'
    parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='checks-', dir=parent))
    program = write_program(directory)
    baseline = directory/'reference-cpu.vhd'
    baseline.write_text(observed_cpu((UPSTREAM/'rtl/cpu.vhd').read_text()))
    lean = no_turbo_cache_source(directory/'no-turbo-cache')
    lean_text = observed_cpu(Path(lean['path']).read_text())
    bench = directory/'tb_cpu_cache_contract.vhd'
    bench.write_text(paired_bench())
    result = {'pin': PIN, 'directory': str(directory), 'program': program, 'runs': [],
              'fault_rejections': [], 'falling_trial': [], 'selected': False,
              'address_model_trial': [],
              'area_saving_measured': False, 'game_verified': False, 'hardware_verified': False,
              'source_sha256': {str(p): digest(p) for p in (
                  ROOT/'scripts/run_cpu_cache_contract.py', ROOT/'scripts/cpu_cache_program.py',
                  ROOT/'sim/reference/tb_cpu_cache_contract.vhd', UPSTREAM/'rtl/cpu.vhd',
                  ROOT/'sim/reference/cpu_tag_phase_model.vhd', UPSTREAM/'rtl/dpram.vhd',
                  UPSTREAM/'sim/system/src/mem/dpram.vhd', UPSTREAM/'sim/system/src/mem/RamMLAB.vhd',
                  UPSTREAM/'rtl/memorymux.vhd', UPSTREAM/'rtl/sdram.sv', baseline, bench,
                  *map(Path, (program['ram'],program['rom'],program['expected'])))},
              'ghdl': subprocess.check_output([ghdl,'--version'], text=True).splitlines()[0]}

    def command(argv):
        run = subprocess.run([ghdl, *argv], cwd=directory, capture_output=True, text=True, timeout=120)
        if run.returncode:
            raise RuntimeError(run.stdout+run.stderr)

    def analyze(library, path):
        command(['-a','--std=08','-frelaxed',f'--work={library}',str(path)])

    analyze('mem', UPSTREAM/'sim/system/src/mem/RamMLAB.vhd')
    for library in ('work','candidate'):
        analyze(library, UPSTREAM/'sim/system/src/mem/dpram.vhd')
        for file in ('export.vhd','divider.vhd','datacache.vhd'):
            analyze(library, UPSTREAM/'rtl'/file)
    analyze('work', baseline)
    analyze('candidate', ROOT/'sim/reference/cpu_tag_phase_model.vhd')

    def select(mode, invalidate_fault=False):
        path = directory/f'candidate-{mode}{"-invalidate-fault" if invalidate_fault else ""}.vhd'
        text = tag_trial(lean_text, mode)
        if invalidate_fault:
            marker = "      if (writebackInvalidateCacheEna = '1') then\n         tag_wren_a   <= '1';"
            if text.count(marker) != 1:
                raise ValueError('invalidate fault marker changed')
            text = text.replace(marker, marker.replace("tag_wren_a   <= '1'", "tag_wren_a   <= '0'"))
        path.write_text(text)
        result['source_sha256'][str(path)] = digest(path)
        analyze('candidate', path)
        analyze('work', bench)
        command(['-e','--std=08','-frelaxed','tb_cpu_cache_contract'])

    def run(name, seed=1, pauses=False, early=False, resets=False, fill_fault=False):
        argv = ['-r','--std=08','-frelaxed','tb_cpu_cache_contract',f'-gSEED={seed}',
                f'-gEXPECTED_COUNT={program["expected_checks"]}', f'-gPAUSES={str(pauses).lower()}',
                f'-gEARLY_DONE={str(early).lower()}',f'-gRESETS={str(resets).lower()}',
                f'-gTRACE={str(args.trace).lower()}', f'-gFILL_FAULT={str(fill_fault).lower()}',
                '--assert-level=error','--ieee-asserts=disable-at-0']
        completed = subprocess.run([ghdl, *argv], cwd=directory, capture_output=True, text=True, timeout=120)
        log = directory/f'{name}.log'
        log.write_text(completed.stdout+completed.stderr)
        passed = completed.returncode==0 and 'PASS CPU cache' in completed.stdout
        # Only the PASS line defines decimal counters. Assertion diagnostics
        # contain hexadecimal got/expected values and must not be parsed as
        # decimal counter metadata (or as a prefix of a hexadecimal address).
        marker = re.search(r'PASS CPU cache[^\n]*',completed.stdout)
        metrics = {key:int(value) for key,value in re.findall(r'([a-z_]+)=(\d+)',marker[0])} if marker else {}
        row = {'name': name, 'seed': seed, 'pauses': pauses, 'early_done': early,
               'resets': resets, 'exit_code': completed.returncode, 'passed': passed,
               'metrics': metrics, 'log': str(log), 'stdout_tail': log.read_text()[-2500:]}
        print(json.dumps(row), flush=True)
        return row

    try:
        select('original')
        profiles = [(1,False,False,False)] if args.quick else [
            (1,False,False,False),(7,True,False,False),
            (19,True,True,False),(31,True,True,True)]
        for seed,pauses,early,resets in profiles:
            row = run(f'original-seed{seed}',seed,pauses,early,resets)
            result['runs'].append(row)
            if not row['passed']:
                raise RuntimeError('original cached CPU contract failed; see '+row['log'])
        if not args.quick:
            for mode, invalidation, fill_fault in (
                ('wrong-address',False,False), ('rising',False,False),
                ('original',True,False), ('original',False,True)):
                select(mode, invalidation)
                row = run('fault-'+mode+('-invalidation' if invalidation else '')+('-fill' if fill_fault else ''),
                          fill_fault=fill_fault)
                row['rejected'] = not row['passed'] and row['exit_code']!=0
                result['fault_rejections'].append(row)
                if not row['rejected']:
                    raise RuntimeError('injected CPU cache fault escaped: '+row['name'])
        select('falling')
        for seed,pauses,early,resets in profiles:
            row = run(f'falling-seed{seed}',seed,pauses,early,resets)
            result['falling_trial'].append(row)
        select('falling-address-model')
        for seed,pauses,early,resets in profiles:
            row = run(f'address-model-seed{seed}',seed,pauses,early,resets)
            result['address_model_trial'].append(row)
        result['falling_edge_cycle_equivalence_verified'] = all(row['passed'] for row in result['falling_trial'])
        result['registered_address_model_verified'] = all(row['passed'] for row in result['address_model_trial'])
        result['all_sources_unchanged'] = all(digest(Path(p))==value for p,value in result['source_sha256'].items())
        result['fault_coverage_complete'] = not args.quick
        result['passed'] = all(row['passed'] for row in result['runs']) and all(
            row['rejected'] for row in result['fault_rejections']) and result['all_sources_unchanged'] and result[
                'falling_edge_cycle_equivalence_verified'] and result['registered_address_model_verified']
        if not result['passed']:
            raise RuntimeError('CPU cache trial rejected; see report')
    finally:
        report = directory/'report.json'
        report.write_text(json.dumps(result,indent=2)+'\n')
        print('report='+str(report), flush=True)


if __name__ == '__main__':
    main()
