#!/usr/bin/env python3
"""Run the connected PSX RTL with functional external memory and local boot data."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import time

from prepare_cd_arithmetic import cd_arithmetic_source
from prepare_no_turbo_cache import no_turbo_cache_source
from prepare_resource_targets import UPSTREAM, digest, prepare
from prepare_system_sim import observed_system_source, prepare_memory_models, compatible_cd_source, compatible_async_video_source
from reference_cpu_gte_program import write_program
from run_reference_checks import gpu_simulation_source, spu_simulation_source
from system_sim_bench import system_bench
from tool_paths import ROOT, find_tool


def unlimited_child_stack():
    resource.setrlimit(resource.RLIMIT_STACK,(resource.RLIM_INFINITY,resource.RLIM_INFINITY))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bios',type=Path,help='Optional private 512 KiB BIOS; default uses authored reset diagnostic')
    parser.add_argument('--ghdl',type=Path,help='Explicit compiler, including optional GCC/LLVM backend')
    parser.add_argument('--output',type=Path,default=ROOT/'build/sim/system')
    parser.add_argument('--max-cycles',type=int,default=2000000)
    parser.add_argument('--trace-limit',type=int,default=10000)
    parser.add_argument('--timeout',type=int,default=900,help='Wall seconds for execution')
    args=parser.parse_args()
    if args.max_cycles<=0 or args.timeout<=0 or args.trace_limit<0: parser.error('Invalid diagnostic bounds')
    ghdl=str(args.ghdl.resolve()) if args.ghdl else find_tool('ghdl')
    if not ghdl: parser.error('GHDL is required')
    version=subprocess.check_output([ghdl,'--version'],text=True)
    optimize=['-O2'] if 'GCC' in version or 'LLVM' in version else []
    directory=args.output.resolve(); directory.mkdir(parents=True,exist_ok=True)
    if directory.is_relative_to(ROOT):
        ignored=subprocess.run(['git','check-ignore','--quiet',str(directory/'boot.hex8')],cwd=ROOT)
        if ignored.returncode: parser.error('Boot data and traces must use a Git-ignored output directory')
    program=write_program(directory/'diagnostic',memory_checks=True)
    if args.bios:
        data=args.bios.read_bytes()
        if len(data)!=524288: parser.error('BIOS must be exactly 512 KiB')
    else:
        data=b''.join(int(line,16).to_bytes(4,'little') for line in program['program'].read_text().splitlines())
    image=directory/'boot.hex8'; image.write_text(''.join(f'{byte:02x}\n' for byte in data))
    # All boot data and logs stay in the ignored local build directory.
    (directory/'expected.hex').write_bytes(program['expected'].read_bytes())
    manifest=prepare(directory/'project',['psx_top']); sources=manifest['targets']['psx_top']['quartus_sources'][:-1]
    observed=observed_system_source(directory)
    cpu=no_turbo_cache_source(directory/'cpu-adapted'); cd=cd_arithmetic_source(directory/'cd-adapted')
    cd_compatible=compatible_cd_source(cd,directory)
    video_compatible=compatible_async_video_source(directory)
    memories=prepare_memory_models(directory/'memory'); bench=system_bench(directory)
    substitutions={str(UPSTREAM/'rtl/cpu.vhd'):cpu['path'],str(UPSTREAM/'rtl/cd_top.vhd'):cd_compatible['path'],
        str(UPSTREAM/'rtl/psx_top.vhd'):observed['path'],
        str(UPSTREAM/'rtl/gpu_videoout.vhd'):gpu_simulation_source(UPSTREAM,directory),
        str(UPSTREAM/'rtl/spu_ram.vhd'):spu_simulation_source(UPSTREAM,directory)}
    substitutions[str(UPSTREAM/'rtl/gpu_videoout_async.vhd')]=video_compatible['path']
    simulation_sources=[]; seen_sources=set()
    for source in sources:
        path=source['path']
        if path.endswith(('/dpram.vhd','/RamMLAB.vhd')):
            path=str(UPSTREAM/'sim/system/src/mem'/Path(path).name)
        path=substitutions.get(path,path)
        key=(source['library'],path)
        if key in seen_sources: continue
        seen_sources.add(key)
        simulation_sources.append({'path':path,'library':source['library'],'sha256':digest(Path(path))})
    simulation_sources += [{'path':str(UPSTREAM/'sim/system/src/tb/globals.vhd'),'library':'tb',
        'sha256':digest(UPSTREAM/'sim/system/src/tb/globals.vhd')},*memories,
        {'path':str(bench),'library':'work','sha256':digest(bench)}]
    log=directory/'checks.log'
    with log.open('w') as output:
        seen=set()
        for source in simulation_sources:
            key=(source['library'],source['path'])
            if key in seen: continue
            seen.add(key)
            path=source['path']
            # Snapshot every analyzed source. Compiled backends also require
            # unique basenames when a RAM model belongs to both mem and work.
            copies=directory/'compiled-sources'; copies.mkdir(exist_ok=True)
            copied=copies/(source['library']+'__'+Path(path).name)
            copied.write_bytes(Path(path).read_bytes()); path=str(copied)
            source['compiled_source']=path
            source['compiled_source_sha256']=digest(copied)
            result=subprocess.run([ghdl,'-a',*optimize,'--std=08','-frelaxed',f'--work={source["library"]}',path],
                cwd=directory,stdout=output,stderr=subprocess.STDOUT,timeout=60)
            if result.returncode: raise SystemExit(f'System analysis failed; see {log}')
        print('Connected system and functional memory models analyzed.',flush=True)
        result=subprocess.run([ghdl,'-e',*optimize,'--std=08','-frelaxed','tb_psx_system'],cwd=directory,
            stdout=output,stderr=subprocess.STDOUT,timeout=60)
        if result.returncode: raise SystemExit(f'System elaboration failed; see {log}')
    command=[ghdl,'-r',*optimize,'--std=08','-frelaxed','tb_psx_system','--max-stack-alloc=0',
        f'-gMAX_CYCLES={args.max_cycles}',f'-gTRACE_LIMIT={args.trace_limit}',
        f'-gEXPECTED_CHECKS={program["expected_checks"]}',
        '-gCHECK_DIAGNOSTIC='+('false' if args.bios else 'true'),
        '--assert-level=error','--ieee-asserts=disable-at-0']
    print(f'Running connected RTL: max_cycles={args.max_cycles}, BIOS={bool(args.bios)}',flush=True)
    timed_out=False
    started=time.monotonic()
    with log.open('a') as output:
        try:
            result=subprocess.run(command,cwd=directory,stdout=output,stderr=subprocess.STDOUT,
                timeout=args.timeout,preexec_fn=unlimited_child_stack)
            code=result.returncode
        except subprocess.TimeoutExpired: timed_out=True;code=None
    elapsed=time.monotonic()-started
    identity_verified=all(digest(Path(source['path']))==source['sha256'] and
        digest(Path(source['compiled_source']))==source['compiled_source_sha256']
        for source in simulation_sources)
    boot_identity_verified=image.read_text()==''.join(f'{byte:02x}\n' for byte in data)
    expected_identity_verified=(directory/'expected.hex').read_bytes()==program['expected'].read_bytes()
    contents=log.read_text(errors='replace'); lines=[line for line in contents.splitlines() if 'SYSTEM_RESULT' in line]
    report={'ghdl_version':version,'optimization_flags':optimize,
        'source_manifest':simulation_sources,'cpu_adaptation':cpu,'cd_adaptation':cd,'observed_system':observed,
        'cd_compatibility':cd_compatible,
        'async_video_compatibility':video_compatible,
        'boot_kind':'private BIOS' if args.bios else 'authored diagnostic','boot_bytes':len(data),
        'boot_sha256':hashlib.sha256(data).hexdigest(),'max_cycles':args.max_cycles,'trace_limit':args.trace_limit,
        'exit_code':code,'wall_timeout':timed_out,'results':lines,
        'execution_seconds':elapsed,'source_identity_verified':identity_verified,
        'boot_identity_verified':boot_identity_verified,'expected_identity_verified':expected_identity_verified,
        'diagnostic_passed':not args.bios and code==0 and identity_verified and boot_identity_verified and
            expected_identity_verified and any('completed=true' in line for line in lines),
        'memory_model':'Pinned clocked SDRAM/DDR timing and cache/DMA behavior; portable preload/bounds',
        'reset_memory_preinitialized':True,'bios_boot_verified':False,'game_verified':False,'hardware_verified':False}
    (directory/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print('\n'.join(lines) or '\n'.join(contents.splitlines()[-12:]),flush=True)
    print(f'System log/report: {directory}',flush=True)
    if timed_out or code or not identity_verified or not boot_identity_verified or not expected_identity_verified or (not args.bios and not report['diagnostic_passed']):
        raise SystemExit('Connected system execution did not complete its bounded check')


if __name__=='__main__': main()
