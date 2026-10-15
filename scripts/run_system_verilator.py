#!/usr/bin/env python3
"""Build and execute the connected PSX RTL with checked host memory models."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

from prepare_system_verilator import prepare
from prepare_resource_targets import digest
from reference_cpu_gte_program import write_program
from tool_paths import ROOT,find_tool


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bios',type=Path,help='Private local 512 KiB BIOS (never committed)')
    parser.add_argument('--memory-checks',action='store_true',help='Authored RAM alias/readback and cached-address execution checks')
    parser.add_argument('--output',type=Path,default=ROOT/'build/sim/system-verilator')
    parser.add_argument('--max-cycles',type=int,default=2000000)
    parser.add_argument('--timeout',type=int,default=900)
    parser.add_argument('--full-reset',action='store_true',help='Execute the upstream complete memory clear')
    parser.add_argument('--reuse',action='store_true',help='Reuse only an executable with unchanged build identity')
    args=parser.parse_args()
    if args.max_cycles<=0 or args.timeout<=0:parser.error('Execution bounds must be positive')
    if args.bios and args.memory_checks:parser.error('Select BIOS or authored memory checks')
    directory=args.output.resolve();directory.mkdir(parents=True,exist_ok=True)
    if directory.is_relative_to(ROOT) and subprocess.run(['git','check-ignore','--quiet',str(directory/'boot.hex8')],cwd=ROOT).returncode:
        parser.error('Private inputs and outputs must use a Git-ignored directory')
    ghdl=find_tool('ghdl');verilator=find_tool('verilator') or str(ROOT/'build/tools/oss-cad-suite/bin/verilator')
    if not ghdl or not Path(verilator).is_file():parser.error('GHDL synthesis and Verilator are required')
    models=ROOT/'sim/reference/mister_memory_models.sv';bench=ROOT/'sim/reference/tb_psx_system_fast.sv'
    build_file=directory/'build-manifest.json';verilog=directory/'system.v';executable=directory/'obj/Vtb_psx_fast'
    if args.reuse:
        if not build_file.is_file() or not executable.is_file():parser.error('No previous verified build exists')
        build=json.loads(build_file.read_text());manifest=build['preparation']
        files=manifest['sources']
        identity=all(digest(Path(s['path']))==s['sha256'] and digest(Path(s['original']))==s['original_sha256']
            and ('selected' not in s or digest(Path(s['selected']))==s['selected_sha256']) for s in files)
        identity &= build['models_sha256']==digest(models) and build['bench_sha256']==digest(bench)
        identity &= build['verilog_sha256']==digest(verilog) and build['executable_sha256']==digest(executable)
        identity &= manifest['fast_reset']==(not args.full_reset)
        if not identity:parser.error('Source/build identity changed; rebuild before reusing')
    else:
        manifest=prepare(directory,fast_reset=not args.full_reset)
        with (directory/'build.log').open('w') as log:
            for source in manifest['sources']:
                subprocess.run([ghdl,'-a','--std=08','-frelaxed',f'--work={source["library"]}',source['path']],
                    cwd=directory,stdout=log,stderr=subprocess.STDOUT,timeout=60,check=True)
            with verilog.open('w') as translated:
                subprocess.run([ghdl,'--synth','--std=08','-frelaxed','--vendor-library=ramip','--out=verilog',
                    'psx_mister_psx_top_resource'],cwd=directory,stdout=translated,stderr=log,timeout=120,check=True)
            from ghdl_verilog import normalize_generated_verilog
            manifest['verilog_normalization']=normalize_generated_verilog(verilog)
            print('Connected system converted; building Verilator executable.',flush=True)
            subprocess.run([verilator,'--binary','--timing','-O3','-j','4','-Wno-fatal','--output-split','20000',
                '--top-module','tb_psx_fast','--Mdir',str(directory/'obj'),str(verilog),str(models),str(bench)],
                cwd=directory,stdout=log,stderr=subprocess.STDOUT,timeout=300,check=True)
        build={'preparation':manifest,'models_sha256':digest(models),'bench_sha256':digest(bench),
            'verilog_sha256':digest(verilog),'executable_sha256':digest(executable),
            'ghdl_version':subprocess.check_output([ghdl,'--version'],text=True),
            'verilator_version':subprocess.check_output([verilator,'--version'],text=True).strip()}
        build_file.write_text(json.dumps(build,indent=2)+'\n')
    from ghdl_verilog import normalize_signed_shifts
    if normalize_signed_shifts(verilog.read_text())[1]:
        parser.error('Recorded build contains logical signed shifts; rebuild with the conversion fix')
    if args.memory_checks:
        from reference_system_memory_program import write_program as write_memory_program
        program=write_memory_program(directory/'diagnostic')
    else:program=write_program(directory/'diagnostic',memory_checks=True)
    if args.bios:
        data=args.bios.read_bytes()
        if len(data)!=524288:parser.error('BIOS must be exactly 512 KiB')
    else:data=b''.join(int(line,16).to_bytes(4,'little') for line in program['program'].read_text().splitlines())
    image=directory/'boot.hex8';image.write_text(''.join(f'{x:02x}\n' for x in data))
    expected=directory/'expected.hex';expected.write_bytes(program['expected'].read_bytes())
    image_sha=digest(image);expected_sha=digest(expected);started=time.monotonic();timed_out=False
    command=[str(executable),f'+max_cycles={args.max_cycles}']+(['+bios'] if args.bios else [])
    print(f'Running connected system: cycles={args.max_cycles}, private_BIOS={bool(args.bios)}',flush=True)
    with (directory/'run.log').open('w') as log:
        try:code=subprocess.run(command,cwd=directory,stdout=log,stderr=subprocess.STDOUT,timeout=args.timeout).returncode
        except subprocess.TimeoutExpired:timed_out=True;code=None
    output=(directory/'run.log').read_text(errors='replace')
    identity=all(digest(Path(s['path']))==s['sha256'] and digest(Path(s['original']))==s['original_sha256']
        and ('selected' not in s or digest(Path(s['selected']))==s['selected_sha256']) for s in manifest['sources'])
    identity &= digest(models)==build['models_sha256'] and digest(bench)==build['bench_sha256']
    identity &= digest(executable)==build['executable_sha256'] and digest(verilog)==build['verilog_sha256']
    identity &= digest(image)==image_sha and digest(expected)==expected_sha
    passed=not args.bios and code==0 and identity and 'PASS FAST SYSTEM' in output and '2648' in output
    report={'build_manifest_sha256':digest(build_file),'source_and_input_identity_verified':identity,
        'boot_kind':'private BIOS' if args.bios else 'authored RAM diagnostic' if args.memory_checks else 'authored CPU/GTE diagnostic',
        'diagnostic_scope':{key:value for key,value in program.items() if key not in ('program','expected')},
        'boot_sha256':hashlib.sha256(data).hexdigest(),
        'boot_bytes':len(data),'max_cycles':args.max_cycles,'elapsed_seconds':time.monotonic()-started,
        'exit_code':code,'wall_timeout':timed_out,'stdout':output,'diagnostic_passed':passed,
        'bounded_bios_execution_completed':bool(args.bios) and code==0 and identity and 'BIOS BOUNDED STOP' in output,
        'bios_boot_verified':False,'game_verified':False,'hardware_verified':False,
        'limitations':['Original MiSTer GTE, separate from the smaller fit candidate',
            'No disc provider or mounted memory cards','No GPU/SPU/FMVs behavioral success inferred',
            'Simulation compatibility copies need wider waveform coverage']}
    (directory/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print('\n'.join(output.splitlines()[-12:]),flush=True)
    print(f'Connected system report: {directory}/report.json',flush=True)
    if timed_out or code or not identity or (not args.bios and not passed):raise SystemExit('Connected simulation failed its bounded check')


if __name__=='__main__':main()
