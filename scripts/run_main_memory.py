#!/usr/bin/env python3
"""Real paired-controller/PHY/chip oracle for packets and BIOS ownership."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare_psram_burst import prepare,UPSTREAM
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def bench_text():
    prefix=(ROOT/'sim/memory/tb_psx_psram_burst.sv').read_text().split('    function automatic integer slot',1)[0]
    replacements={
        'module tb_psx_psram_burst;':'module tb_psx_main_memory;',
        'reg valid=0,write=0,last=0,die=0;':'wire valid,write,last,die;',
        'reg [21:0] address=0;':'wire [21:0] address;',
        'reg [31:0] data=0;':'wire [31:0] data;',
        'reg [3:0] mask=0;':'wire [3:0] mask;',
    }
    for before,after in replacements.items():
        if prefix.count(before)!=1:raise ValueError('Main pin fixture changed: '+before)
        prefix=prefix.replace(before,after)
    return prefix+(ROOT/'sim/memory/tb_psx_main_memory_body.sv').read_text()


def chip_text():
    """Extend only sparse storage windows; all independent pin timing unchanged."""
    text=(ROOT/'sim/memory/cellularram_burst_model.sv').read_text()
    replacements={
        'else if(word>=23\'h7ff000) slot=12288+int\'(word-23\'h7ff000);':
            '''else if(word>=23'h7ff000) slot=12288+int'(word-23'h7ff000);
        else if(word>=23'h420000 && word<23'h421000) slot=16384+int'(word-23'h420000);
        else if(word>=23'h440000 && word<23'h441000) slot=20480+int'(word-23'h440000);
        else if(word>=23'h020000 && word<23'h021000) slot=24576+int'(word-23'h020000);
        else if(word>=23'h040000 && word<23'h041000) slot=28672+int'(word-23'h040000);''',
        'memory[0:32767]':'memory[0:65535]',
        'i<16384':'i<32768',
        "w=i<4096 ? i : i<12288 ? 'h3ff000+i-4096 : 'h7ff000+i-12288;":
            "w=i<4096 ? i : i<12288 ? 'h3ff000+i-4096 : i<16384 ? 'h7ff000+i-12288 : i<20480 ? 'h420000+i-16384 : i<24576 ? 'h440000+i-20480 : i<28672 ? 'h020000+i-24576 : 'h040000+i-28672;",
    }
    for before,after in replacements.items():
        if text.count(before)!=1:raise ValueError('Sparse chip storage anchor changed: '+before)
        text=text.replace(before,after)
    return text


def main():
    compiler,runtime=find_tool('iverilog'),find_tool('vvp')
    if not compiler or not runtime:raise ValueError('Icarus required')
    parent=ROOT/'build/sim/main-memory';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    controller=prepare(work/'rtl')
    bench=work/'tb.sv';bench.write_text(bench_text())
    chip=work/'chip.sv';chip.write_text(chip_text())
    sources=[Path(controller['path']),ROOT/'references/MIRLO64/rtl/soc/psram.sv',
        ROOT/'rtl/memory/psx_mem_arb.sv',ROOT/'rtl/pocket/psx_psram_packet.sv',
        ROOT/'rtl/pocket/psx_main_memory.sv',ROOT/'sim/memory/altddio_registered_model.sv',chip,bench]
    inputs=[*sources,UPSTREAM,ROOT/'sim/memory/tb_psx_psram_burst.sv',
        ROOT/'sim/memory/tb_psx_main_memory_body.sv',ROOT/'sim/memory/cellularram_burst_model.sv',
        ROOT/'scripts/prepare_psram_burst.py',Path(__file__)]
    protected={str(p):digest(p) for p in inputs}
    archive=[]
    for i,(original,sha) in enumerate(protected.items()):
        copy=work/'compiled-inputs'/str(i)/Path(original).name
        copy.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,copy)
        archive.append(dict(original=original,path=str(copy),sha256=sha))
    runs=[]
    def run(name,parameters=(),selected=None,marker=None,arguments=()):
        directory=work/name;directory.mkdir();binary=directory/'sim.vvp'
        with (directory/'build.log').open('w') as log:
            subprocess.run([compiler,'-g2012','-s','tb_psx_main_memory',
                *[f'-Ptb_psx_main_memory.{key}={value}' for key,value in parameters],
                '-o',str(binary),*map(str,selected or sources)],cwd=work,
                stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
        result=subprocess.run([runtime,str(binary),*arguments],cwd=work,capture_output=True,text=True,timeout=180)
        log=directory/'run.log';output=result.stdout+result.stderr;log.write_text(output)
        passed=(result.returncode!=0 and marker in output) if marker else (
            result.returncode==0 and output.count('PASS MAIN MEMORY')==1 and 'FATAL' not in output)
        row=dict(name=name,passed=passed,parameters=dict(parameters),arguments=list(arguments),exit_code=result.returncode,
            stdout=output,binary_sha256=digest(binary),log_sha256=digest(log))
        if marker:row['expected_fault_marker']=marker
        print(name,passed,output,flush=True);runs.append(row)
        return passed
    nominal=run('nominal')
    if nominal:
        for name,parameters in [
            ('delayed',[('CLOCK_DELAY',4),('CONTROL_DELAY',3),('RETURN_DELAY',.5)]),
            ('fast-chip',[('DATA_ACCESS',5)])]:
            if not run(name,parameters):break
        run('backend-error',arguments=['+ERROR_TEST'])
        mutations=[
            ('early-response','packet','DRAIN: if(z_idle && !z_rvalid && (write || received==words))',
                'DRAIN: if(write || received==words)','Packet response before physical drain'),
            ('odd-mask','packet','<< (req_addr[0] ? 2:0);','<< 0;','Packet byte/response mismatch'),
            ('die-alias','packet','assign z_die=current_word[22];',"assign z_die=1'b0;",'Packet byte/response mismatch'),
            ('early-owner','fabric','boot_request && guest_quiesced && idle','boot_request && guest_quiesced && initialized',
                'BIOS ownership before guest drain'),
            ('early-startup','packet','assign idle=!reset && initialized && state==IDLE',
                'assign idle=!reset && state==IDLE','BIOS ownership before chip configuration')]
        for name,which,before,after,marker in mutations:
            index=3 if which=='packet' else 4;original=sources[index].read_text()
            if original.count(before)!=1:raise ValueError('Main fault anchor changed: '+name)
            path=work/(name+'.sv');path.write_text(original.replace(before,after))
            selected=[path if i==index else source for i,source in enumerate(sources)]
            run(name,selected=selected,marker=marker)
        path=work/'lost-backend-error.sv';original=sources[3].read_text()
        before='if(wait_error && (state==SEND || state==DRAIN))'
        if original.count(before)!=1:raise ValueError('Backend fault anchor changed')
        path.write_text(original.replace(before,'if(1\'b0 && (state==SEND || state==DRAIN))'))
        run('lost-backend-error',selected=[path if i==3 else source for i,source in enumerate(sources)],
            marker='Packet byte/response mismatch',arguments=['+ERROR_TEST'])
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    report=dict(passed=identity and len(runs)==10 and all(r['passed'] for r in runs),
        source_identity_verified=identity,protected_sha256=protected,compiled_input_archive=archive,
        runs=runs,controller=controller,physical_memory_bytes=33554432,
        single_shared_paired_bus=True,chip_startup_cycles=12000,clock_mhz=62.5,
        limitations=['Authored packets and byte oracle, no BIOS/disc assets',
            'Guest producer quiescence is a synchronous contract, not yet real MiSTer CPU/DMA/cache/CDC proof',
            'Pending read and zero-mask write reset; nonzero partial write reset semantics not established here',
            'Pin model fixed latency/full refresh; electrical/retention/variable-WAIT hardware unverified',
            'No whole-game traffic/deadline or full-top fit proof'],
        CPU_integration_verified=False,hardware_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
