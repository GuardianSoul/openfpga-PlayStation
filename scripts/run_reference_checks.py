#!/usr/bin/env python3
"""Check upstream elaboration and bounded MiSTer GTE/MDEC execution probes."""
import subprocess
import sys
import json
import shutil
import re

from reference_gte_vectors import write_vectors
from reference_mdec_vectors import write_vectors as write_mdec_vectors
from reference_cpu_gte_program import write_program
from prepare_cd_arithmetic import cd_arithmetic_source
from prepare_resource_targets import PERIPHERALS, digest, prepare
from prepare_reset_only import reset_only_source
from tool_paths import ROOT, find_tool


def checked(command, directory, log, timeout=60):
    result = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=timeout)
    with log.open('a') as output:
        output.write(' '.join(command) + '\n' + result.stdout + result.stderr + '\n')
    if result.returncode:
        sys.exit(result.stdout + result.stderr + f'\nReference check failed; log: {log}')
    return result.stdout + result.stderr


def gpu_simulation_source(upstream, output_dir):
    """Avoid GHDL's scalar port-conversion bounds restriction without changing values."""
    original = upstream/'rtl/gpu_videoout.vhd'
    contents = original.read_text()
    expressions = {'to_integer(videoout_request_clkvid.lineDisp)':9,
                   'to_integer(Gun1Y_screen)':2,'to_integer(Gun2Y_screen)':2}
    for expression, count in expressions.items():
        if contents.count(expression)!=count:
            raise ValueError('Pinned GPU conversion expressions changed')
        contents = contents.replace(expression,"integer'("+expression+")")
    contents,count = re.subn(r'(i_pixel_out_x\s*=> )videoout_request_clkvid.xpos,',
                             r"\1integer'(videoout_request_clkvid.xpos),",contents)
    if count!=5:
        raise ValueError('Pinned GPU overlay coordinate associations changed')
    directory=output_dir/'ghdl-compat'; directory.mkdir(exist_ok=True)
    copied=directory/'gpu_videoout.vhd'; copied.write_text(contents)
    shutil.copy2(upstream/'LICENSE',directory/'LICENSE')
    (directory/'manifest.json').write_text(json.dumps({'original':str(original),
        'original_sha256':digest(original),'ghdl_source':str(copied),'sha256':digest(copied),
        'transform':'Explicit integer qualification around 18 scalar associations; coordinate values unchanged',
        'expressions':expressions,'quartus_uses_original':True},indent=2)+'\n')
    return str(copied)


def spu_simulation_source(upstream, output_dir):
    original=upstream/'rtl/spu_ram.vhd'; contents=original.read_text()
    context='LIBRARY altera_mf;\nUSE altera_mf.altera_mf_components.all;'
    if contents.count(context)!=1:
        raise ValueError('Pinned SPU vendor context changed')
    copied=output_dir/'ghdl-compat/spu_ram.vhd'
    copied.parent.mkdir(parents=True,exist_ok=True)
    copied.write_text(contents.replace(context,'-- Unused vendor context omitted for functional RAM simulation.'))
    shutil.copy2(upstream/'LICENSE',copied.parent/'LICENSE')
    (copied.parent/'spu-manifest.json').write_text(json.dumps({'original':str(original),
        'original_sha256':digest(original),'ghdl_source':str(copied),'sha256':digest(copied),
        'transform':'Remove unused altera_mf context; all instantiated RAMs retain upstream functional models',
        'quartus_uses_original':True},indent=2)+'\n')
    return str(copied)


def system_simulation_source(upstream, output_dir):
    original=upstream/'rtl/psx_top.vhd'; contents=original.read_text()
    contents,count=re.subn(r'(savestate_number\s*=> )savestate_number,',
                           r"\1integer'(savestate_number),",contents)
    if count!=2:
        raise ValueError('Pinned system savestate scalar association changed')
    copied=output_dir/'ghdl-compat/psx_top.vhd'
    copied.parent.mkdir(parents=True,exist_ok=True)
    copied.write_text(contents)
    shutil.copy2(upstream/'LICENSE',copied.parent/'LICENSE')
    (copied.parent/'system-manifest.json').write_text(json.dumps({'original':str(original),
        'original_sha256':digest(original),'ghdl_source':str(copied),'sha256':digest(copied),
        'transform':'Explicit integer qualification for two unused savestate scalar associations; values unchanged',
        'quartus_uses_original':True},indent=2)+'\n')
    return str(copied)


def main():
    ghdl, iverilog = find_tool('ghdl'), find_tool('iverilog')
    if not ghdl or not iverilog:
        sys.exit('Reference checks require GHDL and Icarus. See scripts/setup_ghdl.py and setup_iverilog.sh.')
    output_dir = ROOT / 'build/sim/references'
    output_dir.mkdir(parents=True, exist_ok=True)
    log = output_dir / 'checks.log'
    log.write_text('')
    analyzed = set()

    def analyze(library, path):
        # Reanalyzing a shared FIFO/divider invalidates its already analyzed users.
        key = (library, str(path))
        if key not in analyzed:
            checked([ghdl,'-a','--std=08','-frelaxed',f'--work={library}',str(path)],output_dir,log)
            analyzed.add(key)

    resources = prepare(ROOT / 'build/resources')
    upstream = ROOT / 'references/PSX_MiSTer'
    simulation_paths={}

    def analyze_source(source):
        original=source['path']
        if original.endswith(('/dpram.vhd','/RamMLAB.vhd')):
            return
        if original not in simulation_paths:
            if original.endswith('/gpu_videoout.vhd'):
                simulation_paths[original]=gpu_simulation_source(upstream,output_dir)
            elif original.endswith('/spu_ram.vhd'):
                simulation_paths[original]=spu_simulation_source(upstream,output_dir)
            elif original.endswith('/psx_top.vhd'):
                simulation_paths[original]=system_simulation_source(upstream,output_dir)
            else:
                simulation_paths[original]=original
        analyze(source['library'], simulation_paths[original])

    # The CPU uses upstream functional RAM simulation models, not Intel RAM IP.
    cpu_sources = [('mem','sim/system/src/mem/RamMLAB.vhd'),('work','sim/system/src/mem/dpram.vhd'),
                   ('work','rtl/export.vhd'),('work','rtl/divider.vhd'),('work','rtl/datacache.vhd'),('work','rtl/cpu.vhd')]
    for library, source in cpu_sources:
        analyze(library, upstream/source)
    checked([ghdl,'-e','--std=08','-frelaxed','cpu'],output_dir,log)
    print('PASS MiSTer CPU analysis/elaboration with upstream RAM models; no CPU execution test.',flush=True)
    folder = ROOT / 'references/PS-FPGA/rtl/cpu'
    checked([iverilog,'-g2012','-Wall','-s','mpx_core','-I',str(folder),'-o',str(output_dir/'mpx.vvp'),
             *(str(path) for path in sorted(folder.glob('mpx*.v')))],output_dir,log)
    print('PASS PS-FPGA MPX CPU elaboration; no CPU execution test.',flush=True)
    for source in ('pGTE.vhd','gte_mac0.vhd','gte_mac123.vhd','gte_UNRDivide.vhd','gte.vhd'):
        analyze('work', upstream/'rtl'/source)
    resource_gte = resources['targets']['gte']['quartus_sources'][-1]['path']
    analyze('work', resource_gte)
    resource_cpu = resources['targets']['cpu']['quartus_sources'][-1]['path']
    analyze('work', resource_cpu)
    checked([ghdl,'-e','--std=08','-frelaxed',resources['targets']['cpu']['top']],output_dir,log)
    print('PASS resource wrappers elaborate with original CPU/GTE entities.',flush=True)
    # Simulation uses functional RAMs and documented GHDL compatibility copies.
    analyze('mem', upstream/'sim/system/src/mem/dpram.vhd')
    for name in ('gpu','spu','mdec',*PERIPHERALS):
        target = resources['targets'][name]
        for source in target['quartus_sources']:
            analyze_source(source)
        checked([ghdl,'-e','--std=08','-frelaxed',target['top']],output_dir,log)
        print(f'PASS {name.upper()} resource wrapper analysis/elaboration; no functional execution test.',flush=True)
    cd_copy=cd_arithmetic_source(output_dir/'cd-arithmetic')
    for source in (upstream/'sim/system/src/mem/dpram.vhd',upstream/'rtl/cd_xa_zigzag.vhd',
                   upstream/'rtl/cd_xa.vhd',cd_copy['path']):
        analyze('cdadapt',source)
    checked([ghdl,'-e','--std=08','-frelaxed','--work=cdadapt','cd_top'],output_dir,log)
    print('PASS arithmetic-adapted CD entity elaboration with unchanged RAM/FIFO/XA dependencies.',flush=True)
    cluster=prepare(output_dir/'cluster-project',['cluster'])['targets']['cluster']
    analyze('work', cluster['quartus_sources'][-1]['path'])
    checked([ghdl,'-e','--std=08','-frelaxed',cluster['top']],output_dir,log)
    print('PASS five-component cluster elaboration; independent interfaces, no PSX integration.',flush=True)
    system=prepare(output_dir/'system-project',['psx_top'])['targets']['psx_top']
    analyze('work',upstream/'sim/system/src/mem/RamMLAB.vhd')
    for source in system['quartus_sources']:
        analyze_source(source)
    checked([ghdl,'-e','--std=08','-frelaxed',system['top']],output_dir,log)
    print('PASS connected upstream PSX system elaboration; no BIOS/game execution or Pocket platform.',flush=True)
    vectors = output_dir / 'gte-vectors.txt'
    operations, commands, checks = write_vectors(vectors)
    bench = ROOT / 'sim/reference/tb_mister_gte.vhd'
    checked([ghdl,'-a','--std=08','-frelaxed',str(bench)],output_dir,log)
    checked([ghdl,'-e','--std=08','-frelaxed','tb_mister_gte'],output_dir,log)
    result = checked([ghdl,'-r','--std=08','-frelaxed','tb_mister_gte',f'-gVECTOR_FILE={vectors}',
                      '--assert-level=error','--ieee-asserts=disable-at-0'],output_dir,log)
    marker = f'PASS MiSTer GTE operations={operations} commands={commands} checks={checks}'
    if marker not in result:
        sys.exit('GTE did not report the expected completed probe counts.')
    wrapper_marker = 'PASS FF resource GTE wrapper matched upstream busy/results'
    if wrapper_marker not in result:
        sys.exit('Resource GTE wrapper comparison did not complete.')
    print(marker,flush=True)
    print(wrapper_marker,flush=True)
    cpu_folder=output_dir/'cpu-gte'; program=write_program(cpu_folder)
    checked([ghdl,'-a','--std=08','-frelaxed',str(ROOT/'sim/reference/tb_mister_cpu_gte.vhd')],output_dir,log)
    checked([ghdl,'-e','--std=08','-frelaxed','tb_mister_cpu_gte'],output_dir,log)
    cpu_runs=[]
    for seed in (1,7,19):
        result=checked([ghdl,'-r','--std=08','-frelaxed','tb_mister_cpu_gte',
            f'-gPROGRAM_FILE={program["program"]}',f'-gEXPECTED_FILE={program["expected"]}',f'-gSEED={seed}',
            '--assert-level=error','--ieee-asserts=disable-at-0'],output_dir,log,timeout=120)
        marker=f'commands={program["gte_commands"]} checks={program["mathematical_checks"]}'
        if marker not in result or 'PASS MiSTer CPU/GTE' not in result:
            sys.exit('CPU/GTE reset program did not complete')
        line=next(line.split('(report note): ')[-1] for line in result.splitlines() if 'PASS MiSTer CPU/GTE' in line)
        print(line,flush=True); cpu_runs.append({'seed':seed,'stdout':result,'passed':True})
    (cpu_folder/'probe-report.json').write_text(json.dumps({
        'program_sha256':digest(program['program']),'expected_sha256':digest(program['expected']),
        'program_generator_sha256':digest(ROOT/'scripts/reference_cpu_gte_program.py'),
        'bench_sha256':digest(ROOT/'sim/reference/tb_mister_cpu_gte.vhd'),
        'instructions_in_program':program['instructions'],'gte_commands':program['gte_commands'],
        'mathematical_checks':program['mathematical_checks'],'runs':cpu_runs,
        'memory':'Uncached functional bus with variable response delays; no external controller',
        'gte_register_writes_have_two_nop_gap':True,'clock_enable_pauses_tested':False,
        'laxer_adapter_tested':False,'bios_boot_verified':False,'game_verified':False,'hardware_verified':False},indent=2)+'\n')
    vectors=output_dir/'mdec-vectors.txt'
    commands,macroblocks,words=write_mdec_vectors(vectors)
    checked([ghdl,'-a','--std=08','-frelaxed',str(ROOT/'sim/reference/tb_mister_mdec.vhd')],output_dir,log)
    checked([ghdl,'-e','--std=08','-frelaxed','tb_mister_mdec'],output_dir,log)
    result=checked([ghdl,'-r','--std=08','-frelaxed','tb_mister_mdec',f'-gVECTOR_FILE={vectors}',
                    '--assert-level=error','--ieee-asserts=disable-at-0'],output_dir,log)
    marker=f'PASS MDEC DC probes words={words}'
    if marker not in result:
        sys.exit('MDEC probe count did not complete.')
    print(f'{marker} commands={commands} macroblocks={macroblocks}',flush=True)
    reset_folder=output_dir/'reset-only'; reset_source=reset_only_source(reset_folder)
    (reset_folder/'manifest.json').write_text(json.dumps(reset_source,indent=2)+'\n')
    checked([ghdl,'-a','--std=08','--work=optimized',reset_source['path']],output_dir,log)
    checked([ghdl,'-a','--std=08',str(ROOT/'sim/reference/tb_mister_reset_only.vhd')],output_dir,log)
    for args in (['-gROUNDS=2','-gSEED=2'],["-gFASTSIM='1'",'-gROUNDS=2','-gSEED=1']):
        result=checked([ghdl,'-r','--std=08','tb_mister_reset_only',*args,
                        '--assert-level=error','--ieee-asserts=disable-at-0'],output_dir,log,timeout=300)
        if 'PASS reset-only' not in result: sys.exit('Reset specialization comparison did not complete')
        print(next(line.split('(report note): ')[-1] for line in result.splitlines() if 'PASS reset-only' in line),flush=True)
    print(f'Reference logs: {log}',flush=True)


if __name__ == '__main__':
    main()
