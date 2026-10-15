#!/usr/bin/env python3
"""Run an authored system diagnostic with finite PSRAM/SDRAM service models.

Uses an existing verified RTL translation without modifying it or a live run.
This is not a game checkpoint resume or a full-feature release configuration.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from prepare_pocket_memory_models import prepare
from prepare_resource_targets import digest
from reference_cpu_gte_program import write_program
from tool_paths import ROOT,find_tool


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--translated',type=Path,required=True)
    p.add_argument('--output',type=Path,default=ROOT/'build/sim/pocket-system-model')
    p.add_argument('--layout',choices=('independent','paired'),default='paired',
        help='Paired uses one shared x32 PSRAM bus for CPU, SPU and CD stress')
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    directory=Path(tempfile.mkdtemp(prefix='run-',dir=args.output)).resolve()
    original=args.translated.resolve();original_sha=digest(original)
    translated=directory/'system.v';shutil.copy2(original,translated)
    if digest(translated)!=original_sha:raise ValueError('Translation changed during copying')
    model=prepare(directory)
    vram=directory/'pocket_vram_model.sv'
    shutil.copy2(ROOT/'sim/reference/pocket_vram_model.sv',vram)
    bench=directory/'tb_psx_system.sv'
    text=(ROOT/'sim/reference/tb_psx_system_fast.sv').read_text().replace('mister_sdram_model','pocket_psram_model')
    text=text.replace('mister_ddr_model video_memory','pocket_vram_model video_memory')
    text=text.replace('$fatal(1,"DIAGNOSTIC incomplete")',
        '$fatal(1,"DIAGNOSTIC incomplete checks=%0d word=%08x",checks,ram_dataWrite)')
    text=text.replace('.MEMORY_BYTES(524288),.DOREFRESH(0)',
        '.MEMORY_BYTES(524288),.DOREFRESH(0),.CD_BUS_STRESS(1)')
    if args.layout=='paired':
        text=text.replace('pocket_psram_model #(',
            'pocket_psram_model #(.HALFWORDS_PER_TICK(2),.SHARED_BUS(0),')
    text=text.replace('endmodule','''final begin
 $display("MEMORY_BUDGET main_bytes=%0d main_busy=%0d spu_cd_bytes=%0d spu_busy=%0d vram_bytes=%0d vram_busy=%0d refresh=%0d",
 main_memory.serviced_bytes,main_memory.occupied_cycles,spu_memory.serviced_bytes,spu_memory.occupied_cycles,
 video_memory.serviced_bytes,video_memory.busy_core_cycles,video_memory.refreshes);
end
endmodule''')
    bench.write_text(text)
    program=write_program(directory/'diagnostic',memory_checks=True)
    if program['expected_checks']!=2648 or program['scratchpad_checks']!=4:
        raise ValueError('System fixture requires the complete GTE and scratchpad oracle')
    data=b''.join(int(line,16).to_bytes(4,'little') for line in program['program'].read_text().splitlines())
    (directory/'boot.hex8').write_text(''.join(f'{x:02x}\n' for x in data))
    shutil.copy2(program['expected'],directory/'expected.hex')
    files=[translated,Path(model['path']),vram,bench]
    identities={str(x):digest(x) for x in files}
    verilator=find_tool('verilator') or str(ROOT/'build/tools/oss-cad-suite/bin/verilator')
    if not Path(verilator).is_file():p.error('Verilator is required')
    print(directory,flush=True)
    with (directory/'build.log').open('w') as log:
        subprocess.run([verilator,'--binary','--timing','-O3','-j','4','-Wno-fatal',
            '--output-split','20000','--top-module','tb_psx_fast','--Mdir',str(directory/'obj'),
            *map(str,files)],cwd=directory,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
    with (directory/'run.log').open('w') as log:
        result=subprocess.run([str(directory/'obj/Vtb_psx_fast'),'+max_cycles=30000000'],
            cwd=directory,stdout=log,stderr=subprocess.STDOUT,timeout=900)
    output=(directory/'run.log').read_text();identity=all(digest(Path(f))==h for f,h in identities.items())
    passed=result.returncode==0 and identity and 'PASS FAST SYSTEM' in output
    report={'passed':passed,'source_identity_verified':identity,'source_hashes':identities,
        'original_translation_sha256':original_sha,'memory_profile':model,
        'PSRAM_layout':args.layout,
        'VRAM_profile':'125 MHz x16 SDRAM, CL3, row/turnaround/refresh/CDC budgets; writes consume bandwidth',
        'scope':'authored 2648-result CPU/GTE diagnostic; no game or hardware claim',
        'exit_code':result.returncode,'stdout':output,'checkpoint_resume_verified':False}
    (directory/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(output[-1800:]);print(directory/'report.json')
    if not passed:raise SystemExit('Pocket memory model diagnostic failed; inspect the first mismatch')


if __name__=='__main__':main()
