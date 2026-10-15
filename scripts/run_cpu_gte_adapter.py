#!/usr/bin/env python3
"""Execute original MiSTer CPU plus candidate GTE with portable mixed HDL tools."""
import json
import shutil
import subprocess

from prepare_gte_adapter import adapter_sources, UPSTREAM as GTE
from prepare_resource_targets import UPSTREAM, digest, hardware_text
from reference_cpu_gte_program import write_program
from tool_paths import ROOT, find_tool


def main(name='cpu-gte-adapter', transform=None):
    ghdl=find_tool('ghdl'); verilator=shutil.which('verilator')
    if not verilator:
        path=ROOT/'build/tools/oss-cad-suite/bin/verilator'
        if path.is_file(): verilator=str(path)
    if not ghdl or not verilator: raise SystemExit('GHDL synthesis and Verilator are required')
    directory=ROOT/'build/sim'/name; directory.mkdir(parents=True,exist_ok=True)
    shutil.copy2(UPSTREAM/'LICENSE',directory/'COPYING.GPL2')
    # GHDL cannot synthesize package signals, which only serve upstream debug.
    package=UPSTREAM/'rtl/export.vhd'; text=package.read_text().split('end package;')[0]
    marker='   signal regs                   : tExportRegs;'
    if text.count(marker)!=1: raise ValueError('Pinned CPU export package changed')
    copied=directory/'export.vhd'; copied.write_text(text.replace(marker,'')+'end package;\n')
    cpu=directory/'cpu.vhd'; original=UPSTREAM/'rtl/cpu.vhd'; cpu.write_text(hardware_text(original.read_text()))
    sources=[('mem',UPSTREAM/'sim/system/src/mem/RamMLAB.vhd'),('work',UPSTREAM/'sim/system/src/mem/dpram.vhd'),
        ('work',copied),('work',UPSTREAM/'rtl/divider.vhd'),('work',UPSTREAM/'rtl/datacache.vhd'),('work',cpu)]
    log=directory/'checks.log'; verilog=directory/'cpu.v'
    with log.open('w') as output:
        for library,path in sources:
            subprocess.run([ghdl,'-a','--std=08','-frelaxed',f'--work={library}',str(path)],cwd=directory,
                stdout=output,stderr=subprocess.STDOUT,timeout=60,check=True)
        with verilog.open('w') as translated:
            subprocess.run([ghdl,'--synth','--std=08','-frelaxed','--out=verilog','cpu'],cwd=directory,
                stdout=translated,stderr=output,timeout=120,check=True)
    from ghdl_verilog import normalize_generated_verilog
    normalization=normalize_generated_verilog(verilog)
    adapted=adapter_sources(directory/'adapted')
    if transform: transform(adapted)
    program=write_program(directory/'cpu-gte')
    top=ROOT/'sim/reference/cpu_gte_adapter_top.sv'; harness=ROOT/'sim/reference/cpu_gte_adapter_probe.cpp'
    obj=directory/'obj_dir'
    with log.open('a') as output:
        subprocess.run([verilator,'--cc','--exe','--build','-Wno-fatal','-j','4',
            '--top-module','cpu_gte_adapter_top','-I'+str(GTE/'rtl'),'--Mdir',str(obj),str(verilog),
            *[s['path'] for s in adapted if s['assignment']=='SYSTEMVERILOG_FILE'],str(top),str(harness)],
            cwd=directory,stdout=output,stderr=subprocess.STDOUT,timeout=600,check=True)
    runs=[]
    for seed in (1,7,19):
        result=subprocess.run([str(obj/'Vcpu_gte_adapter_top'),str(program['program']),str(program['expected']),str(seed)],
            cwd=directory,capture_output=True,text=True,timeout=120)
        print(result.stdout+result.stderr,end='',flush=True)
        with log.open('a') as output: output.write(result.stdout+result.stderr)
        runs.append({'seed':seed,'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr,
                     'passed':result.returncode==0 and 'commands=448 checks=2644' in result.stdout})
    report={'verilog_normalization':normalization,
        'ghdl_version':subprocess.check_output([ghdl,'--version'],text=True).splitlines()[0],
        'verilator_version':subprocess.check_output([verilator,'--version'],text=True).strip(),
        'cpu_sources':[{'path':str(p),'library':lib,'sha256':digest(p)} for lib,p in sources],
        'cpu_original_sha256':digest(original),'export_original_sha256':digest(package),
        'cpu_transcode_sha256':digest(verilog),'gte_sources':adapted,
        'top_sha256':digest(top),'harness_sha256':digest(harness),'program_sha256':digest(program['program']),
        'program_generator_sha256':digest(ROOT/'scripts/reference_cpu_gte_program.py'),
        'expected_sha256':digest(program['expected']),'runs':runs,'passed':all(r['passed'] for r in runs),
        'clock_enable_pauses_tested':False,'cached_execution_verified':False,'bios_boot_verified':False,
        'game_verified':False,'hardware_verified':False}
    (directory/'probe-report.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['passed']: raise SystemExit('CPU/GTE adapter execution failed')


if __name__=='__main__': main()
