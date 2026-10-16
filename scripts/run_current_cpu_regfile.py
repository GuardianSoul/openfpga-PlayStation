#!/usr/bin/env python3
"""Exercise a private GPR bank move against the exact frozen current CPU.

Reuses the cached CPU diagnostic's independent result stores and consumer/
retirement comparisons. Two portable SDP read models, four memory/reset/CE
profiles, and three actual datapath faults. No BIOS or game images are used.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

import run_cpu_cache_contract as contract
from measure_pocket_seeds import inputs
from prepare_cpu_regfile_ram import regfile_ram_text
from prepare_dpram_sdp import primitive
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import UPSTREAM, digest
from tool_paths import ROOT, find_tool


PROFILES = [(1, False, False, False), (7, True, False, False),
            (19, True, True, False), (31, True, True, True)]
ADDRESS_MODEL = '''architecture model of pocket_dpram_sdp is
    type memory is array(0 to 2**addr_width-1) of std_logic_vector(data_width-1 downto 0);
    signal ram : memory := (others=>(others=>'0'));
    signal held_address : std_logic_vector(addr_width-1 downto 0) := (others=>'0');
begin
    process(clock_a) begin
        if rising_edge(clock_a) and clken_a='1' then
            if wren_a='1' then ram(to_integer(unsigned(address_a)))<=data_a; end if;
            q_a<=ram(to_integer(unsigned(address_a)));
        end if;
    end process;
    process(clock_b) begin
        if rising_edge(clock_b) and clken_b='1' then
            assert wren_b='0' report "SDP B must be read-only" severity failure;
            held_address<=address_b;
        end if;
    end process;
    q_b<=ram(to_integer(unsigned(held_address)));
end architecture;
'''


def trial(text, mode):
    if mode == 'control':
        return text
    text = regfile_ram_text(text)
    if mode == 'rising':
        old, new, count = 'clock_b => not clk1x, address_b => regs', 'clock_b => clk1x, address_b => regs', 2
    elif mode == 'wrong-address':
        old, new, count = 'address_b => regs1_address_b,', 'address_b => regs1_address_b xor "00001",', 1
    elif mode == 'wrong-write':
        old, new, count = 'data_a => regs_data_a, wren_a => regs_wren_a,', 'data_a => regs_data_a xor x"00000001", wren_a => regs_wren_a,', 2
    else:
        if mode != 'falling':
            raise ValueError('Unknown CPU trial')
        return text
    if text.count(old) != count:
        raise ValueError('CPU fault anchor changed')
    return text.replace(old, new)


def paired_bench():
    original = contract.ports
    contract.ports = lambda name: original(name)+([('mem_writeRequest', 'out', 'std_logic')] if name == 'cpu' else [])
    try:
        text = contract.paired_bench()
    finally:
        contract.ports = original
    marker = '            assert request=candidate_request'
    if text.count(marker) != 1:
        raise ValueError('Current CPU short-write comparison anchor changed')
    text = text.replace(marker, '            assert mem_writeRequest=candidate_mem_writeRequest report "CPU short write mismatch" severity failure;\n'+marker, 1)
    # Compare the decode values at their consumer as well as later retirement;
    # bypass can otherwise conceal an incorrect bank read in a small program.
    text = text.replace('\nbegin\n', '\n    signal probe_gpr, candidate_probe_gpr : std_logic_vector(63 downto 0);\nbegin\n', 1)
    for prefix in ('', 'candidate_'):
        anchor = 'probe_invalidate=>'+prefix+'probe_invalidate'
        if text.count(anchor) != 1:
            raise ValueError('CPU GPR probe connection changed')
        text = text.replace(anchor, anchor+',\n        probe_gpr=>'+prefix+'probe_gpr', 1)
    for old, new in [('variable enabled : boolean;', 'variable enabled, decode_accepted : boolean;'),
                     ("enabled:=ce='1';", "enabled:=ce='1'; decode_accepted:=probe_accept='1';"),
                     ('        wait for 1 ps;', '        wait for 1 ps;\n        if decode_accepted then\n            assert probe_gpr=candidate_probe_gpr report "CPU decode GPR mismatch" severity failure;\n        end if;')]:
        if text.count(old) != 1:
            raise ValueError('CPU decode comparison anchor changed')
        text = text.replace(old, new, 1)
    return text


def observe(text):
    text = contract.observed_cpu(text)
    old = '      probe_invalidate : out std_logic;\n'
    if text.count(old) != 1:
        raise ValueError('CPU GPR observation ABI changed')
    text = text.replace(old, old+'      probe_gpr : out std_logic_vector(63 downto 0);\n', 1)
    old = "   probe_invalidate <= writebackInvalidateCacheEna;\n"
    if text.count(old) != 1:
        raise ValueError('CPU GPR observation body changed')
    return text.replace(old, old+'   probe_gpr <= std_logic_vector(decodeValue1) & std_logic_vector(decodeValue2);\n', 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    project, work = args.project.resolve(), args.output.resolve()
    if work.exists() or not work.is_relative_to(ROOT/'build') or subprocess.run(
            ['git', 'check-ignore', '--quiet', str(work/'report.json')], cwd=ROOT).returncode:
        raise ValueError('Fresh ignored output directory required')
    initial = inputs(project)
    target = json.loads((project/'timing-inputs.json').read_text())
    if initial != target['quartus_sources'] or any(digest(project/('apf_top'+ext)) != sha
            for ext, sha in target['project_sha256'].items()):
        raise ValueError('Frozen CPU project/source identities differ')
    selected = assignments(project, (project/'apf_top.qsf').read_text())
    def one(name):
        paths = {p for _, p in selected if p.name == name}
        if len(paths) != 1:
            raise ValueError('Expected one selected '+name)
        return next(iter(paths))
    core = one('cpu.vhd').read_text()
    regfile_ram_text(core)  # Exact reviewed CPU guard before any model changes.
    for name, reverse in [('pocket_dpram_sdp.vhd', False), ('pocket_dpram_sdp_reverse.vhd', True)]:
        if one(name).read_text() != primitive((UPSTREAM/'rtl/dpram.vhd').read_text(), reverse):
            raise ValueError('Selected SDP primitive differs from reviewed API/register modes')
    work.mkdir(parents=True)
    sources = [one(n) for n in ('cpu.vhd', 'export.vhd', 'divider.vhd', 'datacache.vhd',
                               'pocket_dpram_sdp.vhd', 'pocket_dpram_sdp_reverse.vhd')]
    sources += [UPSTREAM/p for p in ('sim/system/src/mem/dpram.vhd', 'sim/system/src/mem/RamMLAB.vhd', 'rtl/cpu.vhd', 'rtl/dpram.vhd')]
    sources += [ROOT/'sim/reference/tb_cpu_cache_contract.vhd', project/'apf_top.qsf',
                project/'apf_top.qpf', project/'timing-inputs.json']
    sources += [ROOT/'scripts'/n for n in ('run_current_cpu_regfile.py', 'prepare_cpu_regfile_ram.py',
                'run_cpu_cache_contract.py', 'cpu_cache_program.py', 'prepare_resource_targets.py',
                'prepare_dpram_sdp.py', 'prepare_pocket_config_cdc.py', 'measure_pocket_seeds.py', 'tool_paths.py')]
    protected, archive = {}, []
    for index, source in enumerate(sources):
        copy = work/'input-archive'/str(index)/source.name
        copy.parent.mkdir(parents=True)
        sha = digest(source)
        shutil.copyfile(source, copy)
        if digest(source) != sha or digest(copy) != sha:
            raise ValueError('CPU trial input changed during capture')
        protected[str(source)] = sha
        archive.append(dict(source=str(source), copy=str(copy), sha256=sha))
    ghdl = find_tool('ghdl')
    report = dict(project=str(project), selected_cpu_sha256=digest(one('cpu.vhd')), input_archive=archive,
                  runs=[], production_selected=False, fit_verified=False, timing_verified=False,
                  game_verified=False, hardware_verified=False)
    for model in ('registered-data', 'registered-address'):
        directory = work/model
        directory.mkdir()
        program = contract.write_program(directory)
        report['program'] = program
        ram = (UPSTREAM/'sim/system/src/mem/dpram.vhd').read_text()
        ram = ram[:ram.index('end rtl;')+len('end rtl;')]
        ram = re.sub(r'\bdpram\b', 'pocket_dpram_sdp', ram, flags=re.I)
        (directory/'pocket_dpram_sdp_reverse.vhd').write_text(ram.replace('pocket_dpram_sdp', 'pocket_dpram_sdp_reverse'))
        if model == 'registered-address':
            ram = ram[:ram.index('architecture rtl of')]+ADDRESS_MODEL
        (directory/'pocket_dpram_sdp.vhd').write_text(ram)
        (directory/'baseline.vhd').write_text(observe(core))
        (directory/'bench.vhd').write_text(paired_bench())
        def command(argv):
            run = subprocess.run([ghdl, *argv], cwd=directory, capture_output=True, text=True, timeout=120)
            with (directory/'build.log').open('a') as log:
                log.write(' '.join(argv)+'\n'+run.stdout+run.stderr)
            if run.returncode:
                raise RuntimeError(run.stdout+run.stderr)
        def analyze(library, path):
            command(['-a', '--std=08', '-frelaxed', '--work='+library, str(path)])
        analyze('mem', UPSTREAM/'sim/system/src/mem/RamMLAB.vhd')
        for library in ('work', 'candidate'):
            for path in [UPSTREAM/'sim/system/src/mem/dpram.vhd', directory/'pocket_dpram_sdp.vhd',
                         directory/'pocket_dpram_sdp_reverse.vhd', one('export.vhd'), one('divider.vhd'), one('datacache.vhd')]:
                analyze(library, path)
        analyze('work', directory/'baseline.vhd')
        for mode in ('control', 'falling', 'rising', 'wrong-address', 'wrong-write'):
            path = directory/(mode+'.vhd')
            path.write_text(observe(trial(core, mode)))
            analyze('candidate', path)
            analyze('work', directory/'bench.vhd')
            command(['-e', '--std=08', '-frelaxed', 'tb_cpu_cache_contract'])
            for seed, pause, early, resets in PROFILES:
                argv = ['-r', '--std=08', '-frelaxed', 'tb_cpu_cache_contract', f'-gSEED={seed}',
                        f'-gEXPECTED_COUNT={program["expected_checks"]}', f'-gPAUSES={str(pause).lower()}',
                        f'-gEARLY_DONE={str(early).lower()}', f'-gRESETS={str(resets).lower()}',
                        '--assert-level=error', '--ieee-asserts=disable-at-0']
                run = subprocess.run([ghdl, *argv], cwd=directory, capture_output=True, text=True, timeout=120)
                text = run.stdout+run.stderr
                log = directory/f'{mode}-{seed}.log'
                log.write_text(text)
                marker = re.search(r'PASS CPU cache[^\n]*', text)
                metrics = {key: int(value) for key, value in re.findall(r'([a-z_]+)=(\d+)', marker[0])} if marker else {}
                first = next((line for line in text.splitlines() if 'mismatch' in line.lower()), '')
                passed = run.returncode == 0 and bool(marker) and metrics.get('checks') == program['expected_checks']
                expected = passed if mode in ('control', 'falling') else run.returncode != 0 and bool(first)
                report['runs'].append(dict(model=model, mode=mode, seed=seed, pauses=pause, early_done=early,
                    resets=resets, exit_code=run.returncode, passed=passed, expected_verdict=expected,
                    first_mismatch=first, metrics=metrics, log=str(log), log_sha256=digest(log)))
                (work/'report.json').write_text(json.dumps(report, indent=2)+'\n')
                print(model, mode, seed, 'expected_verdict', expected, first, flush=True)
        for path in directory.glob('*'):
            if path.suffix in ('.vhd', '.hex'):
                protected[str(path)] = digest(path)
    identities = inputs(project) == initial and all(digest(Path(p)) == sha for p, sha in protected.items())
    identities &= all(digest(Path(row['copy'])) == row['sha256'] for row in archive)
    report.update(passed=identities and len(report['runs']) == 40 and all(row['expected_verdict'] for row in report['runs']),
        source_identity_verified=identities, protected_sha256=protected,
        ghdl_version=subprocess.check_output([ghdl, '--version'], text=True).splitlines()[0],
        scope='Authored cached CPU program, ideal phased 1x/2x/3x clocks and CPU-port memory model; no PHY, GTE commands, game or hardware',
        model_adaptations='Portable upstream RAM and read-only-B SDP alias on both CPUs; alternative holds B address with unregistered Q, including tag/cache reads; reverse SDP uses the common portable model')
    (work/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
