#!/usr/bin/env python3
"""Cycle-compare lean release initialization against the pinned reset sequence."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_release_init import release_init_source
from prepare_resource_targets import UPSTREAM,digest
from tool_paths import ROOT,find_tool


def bench_text():
    text=(ROOT/'sim/reference/tb_mister_reset_only.vhd').read_text()
    text=text.replace('SEED : natural := 0);',"SEED : natural := 0; SPUSDRAM : std_logic := '1'; PAUSE_DELAY : natural := 49);")
    text=text.replace("useSPUSDRAM=>'1'",'useSPUSDRAM=>SPUSDRAM')
    text=text.replace('variable reads,vram_words,spu_words,ram_words,scratch_words : natural := 0;',
        'variable reads,vram_words,spu_words,ram_words,scratch_words,internal_spu : natural := 0;')
    text=text.replace('expected_vram:=131072; expected_spu:=262144; expected_ram:=524288;',
        "expected_vram:=131072; expected_spu:=262144; expected_ram:=524288;\n            if SPUSDRAM='0' then expected_spu:=34816;end if;")
    text=text.replace('ticks-start_tick=49','ticks-start_tick=PAUSE_DELAY')
    before='''                    assert to_integer(unsigned(o(0).ddr3_ADDR))=2*(vram_words mod expected_vram)
                        report "Reset VRAM address sequence" severity failure;'''
    after='''                    if SPUSDRAM='0' and unsigned(o(0).ddr3_ADDR)>=16#C0400# then
                        assert to_integer(unsigned(o(0).ddr3_ADDR))=16#C0400#+2*(internal_spu mod 56832)
                            report "Reset internal SPU address sequence" severity failure;
                        internal_spu:=internal_spu+1;
                    else
                        assert to_integer(unsigned(o(0).ddr3_ADDR))=2*(vram_words mod expected_vram)
                            report "Reset VRAM address sequence" severity failure;
                        vram_words:=vram_words+1;
                    end if;'''
    if text.count(before)!=1:raise ValueError('Reset DDR oracle changed')
    text=text.replace(before,after).replace('                    vram_words:=vram_words+1;\n                end if;', '                end if;')
    before='''                assert to_integer(unsigned(o(0).SS_SPURAM_Adr))=2*(spu_words mod expected_spu)
                    report "Reset SPU address sequence" severity failure;'''
    after='''                if SPUSDRAM='0' and (spu_words mod expected_spu)>=2048 then
                    assert to_integer(unsigned(o(0).SS_SPURAM_Adr))=2*(229376+(spu_words mod expected_spu)-2048)
                        report "Reset SPU tail sequence" severity failure;
                else
                    assert to_integer(unsigned(o(0).SS_SPURAM_Adr))=2*(spu_words mod expected_spu)
                        report "Reset SPU address sequence" severity failure;
                end if;'''
    if text.count(before)!=1:raise ValueError('Reset SPU oracle changed')
    text=text.replace(before,after)
    text=text.replace('assert o(0).validSStates="1010"',
        "assert (SPUSDRAM='1' or internal_spu=56832*round_no or FASTSIM='1') report \"Reset internal SPU incomplete\" severity failure;\n                    assert o(0).validSStates=\"1010\"")
    return text


def main():
    parent=ROOT/'build/sim/release-init';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));source=release_init_source(work/'rtl')
    bench=work/'tb.vhd';bench.write_text(bench_text());reference=UPSTREAM/'rtl/savestates.vhd'
    protected={str(p):digest(p) for p in [Path(source['path']),reference,bench,
        ROOT/'scripts/prepare_release_init.py',ROOT/'scripts/prepare_reset_only.py',
        ROOT/'sim/reference/tb_mister_reset_only.vhd',Path(__file__)]}
    ghdl=find_tool('ghdl');runs=[]
    def run(name,generics,mutation=None,marker=None):
        directory=work/name;directory.mkdir();selected=Path(source['path'])
        if mutation:
            before,after=mutation;text=selected.read_text()
            if text.count(before)!=1:raise ValueError('Init fault anchor changed')
            selected=directory/'savestates.vhd';selected.write_text(text.replace(before,after))
        commands=[[ghdl,'-a','--std=08',str(reference)],
            [ghdl,'-a','--std=08','--work=optimized',str(selected)],
            [ghdl,'-a','--std=08',str(bench)],[ghdl,'-e','--std=08','tb_mister_reset_only']]
        with (directory/'build.log').open('w') as log:
            for command in commands:subprocess.run(command,cwd=directory,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
        r=subprocess.run([ghdl,'-r','--std=08','tb_mister_reset_only',*generics,
            '--assert-level=error','--ieee-asserts=disable-at-0'],cwd=directory,capture_output=True,text=True,timeout=300)
        output=r.stdout+r.stderr;(directory/'run.log').write_text(output)
        passed=r.returncode!=0 and marker in output if marker else r.returncode==0 and output.count('PASS reset-only')==1 and 'assertion failure' not in output
        runs.append(dict(name=name,generics=generics,passed=passed,exit_code=r.returncode,output=output,
            source_sha256=digest(selected),log_sha256=digest(directory/'run.log')))
        print(name,passed,output,flush=True);return passed
    profiles=[('full-offchip',['-gROUNDS=2','-gSEED=2']),
        ('fast-offchip',["-gFASTSIM='1'",'-gROUNDS=2','-gSEED=1']),
        ('full-internal',["-gSPUSDRAM='0'",'-gROUNDS=1','-gSEED=4']),
        ('retry',["-gFASTSIM='1'",'-gPAUSE_DELAY=2500','-gSEED=3'])]
    ok=True
    for name,args in profiles:ok=run(name,args) and ok
    if ok:
        run('bad-main-end',['-gSEED=2'],("(savetype_counter = 16 and RAMAddrNext = 0)","(savetype_counter = 16 and RAMAddrNext = 2)"),'Reset control mismatch')
        run('bad-SPU-boundary',["-gSPUSDRAM='0'",'-gSEED=4'],('RAMAddrNext < 2048','RAMAddrNext < 1024'),'Reset control mismatch')
        before='unstallwait       <= 1023;'
        run('bad-retry',["-gFASTSIM='1'",'-gPAUSE_DELAY=2500','-gSEED=3'],(before,'unstallwait       <= 511;'),'Reset control mismatch')
    identity=all(digest(Path(p))==h for p,h in protected.items())
    report=dict(passed=identity and len(runs)==7 and all(r['passed'] for r in runs),source_identity_verified=identity,
        protected_sha256=protected,runs=runs,selected_source=source,hardware_verified=False,game_verified=False,
        scope='Every 2x tick and independently counted header/scratch/SPU/VRAM/main clear operations; delayed responses, two resets and late pause retry')
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
