#!/usr/bin/env python3
"""Three packet clients plus native VRAM share one tagged MIRLO SDRAM path."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_shared_sdram import prepare
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def main():
    parent=ROOT/'build/sim/sdram-shared';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    phy=prepare(work/'phy')
    original=ROOT/'sim/memory/sdr_pin_model.sv';text=original.read_text()
    text=text.replace('reg [15:0] memory [0:32767 + EDGE_REGIONS*24576];','reg [15:0] memory [0:24575];')
    start=text.index('    function automatic integer slot(');end=text.index('    function automatic [24:0] fixture_address',start)
    text=text[:start]+'''    function automatic integer slot(input reg [24:0] a);
        integer region;
        case(a[24:12])
            13'h000:region=0;13'h800:region=1;13'h840:region=2;
            13'h400:region=3;13'h480:region=4;13'h500:region=5;
            default:begin region=0;$fatal(1,"Shared SDRAM pin address outside fixture %h",a);end
        endcase
        return region*4096+int'(a[11:0]);
    endfunction
'''+text[end:]
    before='''        for (integer i = 0; i < 32768 + EDGE_REGIONS*24576; i = i + 1)
            memory[i] = {initial_byte(integer'(fixture_address(i))*2+1), initial_byte(integer'(fixture_address(i))*2)};'''
    if text.count(before)!=1:raise ValueError('Chip storage anchor changed')
    text=text.replace(before,'        for(integer i=0;i<24576;i++) memory[i]=0;')
    chip=work/'chip.sv';chip.write_text(text)
    shared=ROOT/'rtl/pocket/psx_sdram_shared.sv'
    sources=[shared,ROOT/'rtl/memory/psx_mem_bridge.sv',ROOT/'rtl/memory/psx_mem_cdc.sv',
        ROOT/'rtl/pocket/psx_spu_sdram_adapter.sv',ROOT/'rtl/pocket/psx_vram_stream.sv',
        ROOT/'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv',
        ROOT/'references/MIRLO64/rtl/soc/sdr_ctrl.sv',Path(phy['path']),
        ROOT/'sim/memory/altddio_clock_model.sv',chip,ROOT/'sim/pocket/tb_sdram_shared.sv']
    protected={str(p):digest(p) for p in [*sources,original,Path(__file__),
        ROOT/'scripts/prepare_shared_sdram.py',ROOT/'references/MIRLO64/rtl/soc/sdr_phy.sv']}
    runs=[];faults=[]
    def run(name,half,changed=None,marker=None):
        selected=list(sources)
        if changed:
            before,after=changed;text=shared.read_text()
            if text.count(before)!=1:raise ValueError('Shared SDRAM fault anchor changed')
            path=work/(name+'.sv');path.write_text(text.replace(before,after));selected[0]=path
        binary=work/(name+'.vvp')
        with (work/(name+'-build.log')).open('w') as log:
            subprocess.run([find_tool('iverilog'),'-g2012','-s','tb_sdram_shared',
                f'-Ptb_sdram_shared.GPU_HALF={half}','-o',str(binary),*map(str,selected)],
                stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
        result=subprocess.run([find_tool('vvp'),str(binary)],cwd=work,capture_output=True,text=True,timeout=180)
        output=result.stdout+result.stderr;log=work/(name+'.log');log.write_text(output)
        passed=result.returncode!=0 and marker in output if marker else result.returncode==0 and output.count('PASS SHARED SDRAM')==1 and 'FATAL' not in output
        record=dict(name=name,passed=passed,exit_code=result.returncode,output=output,
            binary_sha256=digest(binary),log_sha256=digest(log),GPU_half_ns=half)
        (faults if marker else runs).append(record);print(name,passed,output,flush=True);return passed
    if run('nominal',7.381275):
        run('slow-source',29)
        run('wrong-tag',7.381275,('.req_tag({1\'b0,selected})',".req_tag({1'b0,selected+2'd1})"),'Read tag/order mismatch')
        run('early-idle',7.381275,('!return_valid && !return_next && phy_idle','!return_valid && !return_next'),'Physical idle asserted before drain')
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    report=dict(passed=identity and len(runs)==2 and all(r['passed'] for r in runs) and len(faults)==2 and all(f['passed'] for f in faults),
        source_identity_verified=identity,source_sha256=protected,runs=runs,faults=faults,phy=phy,
        topology='CPU/SPU/BIOS packet frontends plus native VRAM stream share tagged32-bit SDRAM commands',
        pin_profile={'tAC_ns':2.5,'tHZ_ns':2.5},hardware_verified=False,full_top_connected=False,
        actual_CPU_SPU_execution_verified=False,game_bandwidth_verified=False,timing_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
