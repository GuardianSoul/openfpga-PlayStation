#!/usr/bin/env python3
"""Check packed division against the exact frozen current CPU and ISA stores.

Two portable RAM models, four cache/CE/memory/reset profiles and actual
shifted-bit/sign/remainder faults. Does not supply GTE/game/PHY or hardware proof.
"""
from pathlib import Path
import sys,json,re,subprocess,shutil
import run_cpu_cache_contract as contract
from cpu_divider_program import write_program
import run_current_cpu_regfile as current
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import UPSTREAM,digest
from tool_paths import ROOT as R, find_tool
from measure_pocket_seeds import inputs
from prepare_dpram_sdp import primitive
from prepare_divider_bank import cpu_profile


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('project',type=Path);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    P=args.project.resolve();C=args.output.resolve()
    if C.exists() or not C.is_relative_to(R/'build') or subprocess.run(['git','check-ignore','--quiet',str(C/'report.json')],cwd=R).returncode:raise ValueError('Fresh ignored output required')
    record=json.loads((P/'timing-inputs.json').read_text())
    if inputs(P)!=record['quartus_sources'] or any(digest(P/('apf_top'+ext))!=sha for ext,sha in record['project_sha256'].items()):
        raise ValueError('Frozen CPU source/project identities differ')
    selected=assignments(P,(P/'apf_top.qsf').read_text())
    def one(name):
     rows={p for _,p in selected if p.name==name};assert len(rows)==1;return rows.pop()
    profile=cpu_profile(one('cpu.vhd'))
    ram_sources=[]
    if profile=='strict-sdp-cache':
     for name,reverse in [('pocket_dpram_sdp.vhd',False),('pocket_dpram_sdp_reverse.vhd',True)]:
      if one(name).read_text()!=primitive((UPSTREAM/'rtl/dpram.vhd').read_text(),reverse):raise ValueError('Selected RAM primitive differs from reviewed CPU model')
      ram_sources.append(one(name))
    elif digest(one('dpram.vhd'))!=digest(UPSTREAM/'rtl/dpram.vhd'):
     raise ValueError('Release CPU DPRAM differs from the pinned primitive')
    else:ram_sources.append(one('dpram.vhd'))
    original=one('cpu.vhd').read_text();shared=(R/'rtl/pocket/psx_divider_bank.vhd').read_text()
    simulation_cpu=original
    if profile=='release-dpram-cache':
     # Bind only the CPU's two one-writer caches to the portable model.
     # The datacache's reverse writer keeps its original pinned RAM model.
     assert original.count('entity work.dpram')==3
     simulation_cpu=original.replace('entity work.dpram','entity work.pocket_dpram_sdp',2)
    C.mkdir(parents=True,exist_ok=False);runs=[];protected={str(p):digest(p) for p in [*[one(n) for n in ('cpu.vhd','export.vhd','divider.vhd','datacache.vhd')],*ram_sources,*[R/'scripts'/n for n in ('prepare_divider_bank.py','cpu_divider_program.py','run_current_cpu_regfile.py','run_cpu_cache_contract.py','cpu_cache_program.py')],R/'sim/reference/tb_cpu_cache_contract.vhd',*[UPSTREAM/n for n in ('sim/system/src/mem/dpram.vhd','sim/system/src/mem/RamMLAB.vhd','rtl/dpram.vhd')],P/'apf_top.qsf',P/'apf_top.qpf',P/'timing-inputs.json',Path(__file__),(R/'rtl/pocket/psx_divider_bank.vhd'),(R/'scripts/cpu_divider_program.py')]}
    archive=[]
    for i,(name,sha) in enumerate(protected.items()):
     path=C/'input-archive'/str(i)/Path(name).name;path.parent.mkdir(parents=True,exist_ok=False);shutil.copyfile(name,path);assert digest(path)==sha;archive.append(dict(source=name,path=str(path),sha256=sha))
    report=dict(project=str(P),selected_cpu_sha256=digest(one('cpu.vhd')),cpu_cache_profile=profile,simulation_cache_binding='Common portable RAM models at unchanged clocks/ports; CPU cache entity names only',packed_divider_sha256=__import__('hashlib').sha256(shared.encode()).hexdigest(),runs=runs,passed=False,hardware_verified=False)
    for model in ('registered-data','registered-address'):
     D=C/model;D.mkdir();program=write_program(D);report['program']=program
     ram=(UPSTREAM/'sim/system/src/mem/dpram.vhd').read_text();ram=ram[:ram.index('end rtl;')+len('end rtl;')];ram=re.sub(r'\bdpram\b','pocket_dpram_sdp',ram,flags=re.I)
     (D/'pocket_dpram_sdp_reverse.vhd').write_text(ram.replace('pocket_dpram_sdp','pocket_dpram_sdp_reverse'))
     if model=='registered-address':ram=ram[:ram.index('architecture rtl of')]+current.ADDRESS_MODEL
     (D/'pocket_dpram_sdp.vhd').write_text(ram);(D/'baseline.vhd').write_text(contract.observed_cpu(simulation_cpu));bench=contract.paired_bench();assert bench.count('EXPECTED_COUNT : positive := 20')==1;bench=bench.replace('EXPECTED_COUNT : positive := 20',f'EXPECTED_COUNT : positive := {program["expected_checks"]}');(D/'bench.vhd').write_text(bench)
     def cmd(argv):
      r=subprocess.run([find_tool('ghdl'),*argv],cwd=D,capture_output=True,text=True,timeout=120)
      with (D/'build.log').open('a') as f:f.write(' '.join(argv)+'\n'+r.stdout+r.stderr)
      if r.returncode:raise RuntimeError(r.stdout+r.stderr)
     def analyze(lib,p):cmd(['-a','--std=08','-frelaxed','--work='+lib,str(p)])
     analyze('mem',UPSTREAM/'sim/system/src/mem/RamMLAB.vhd')
     for lib in ('work','candidate'):
      for p in (UPSTREAM/'sim/system/src/mem/dpram.vhd',D/'pocket_dpram_sdp.vhd',D/'pocket_dpram_sdp_reverse.vhd',one('export.vhd'),one('datacache.vhd')):analyze(lib,p)
     analyze('work',one('divider.vhd'))
     analyze('work',D/'baseline.vhd')
     for mode in ('control','packed','bad-bit','bad-sign','bad-remainder'):
      text=one('divider.vhd').read_text() if mode=='control' else shared
      if mode=='bad-bit':
       old="r_next(0) := work_q(work_q'left);";assert text.count(old)==1;text=text.replace(old,"r_next(0) := not work_q(work_q'left);")
      if mode=='bad-sign':
       old="if (sign_n xor sign_d) = '1' then";assert text.count(old)==1;text=text.replace(old,"if false then")
      if mode=='bad-remainder':
       old="remainder <= signed(r_next(remainder'left+1 downto 1));";assert text.count(old)==1;text=text.replace(old,"remainder <= signed(r_next(remainder'left+1 downto 1)) xor to_signed(1,remainder'length);")
      path=D/(mode+'.vhd');path.write_text(text);analyze('candidate',path)
      cpu=D/'candidate-cpu.vhd';cpu.write_text(contract.observed_cpu(simulation_cpu));analyze('candidate',cpu)
      analyze('work',D/'bench.vhd');cmd(['-e','--std=08','-frelaxed','tb_cpu_cache_contract'])
      profiles=current.PROFILES if mode in ('control','packed') else [current.PROFILES[0]]
      for seed,pause,early,resets in profiles:
       argv=['-r','--std=08','-frelaxed','tb_cpu_cache_contract',f'-gSEED={seed}',f'-gEXPECTED_COUNT={program["expected_checks"]}',f'-gPAUSES={str(pause).lower()}',f'-gEARLY_DONE={str(early).lower()}',f'-gRESETS={str(resets).lower()}','--assert-level=error','--ieee-asserts=disable-at-0']
       r=subprocess.run([find_tool('ghdl'),*argv],cwd=D,capture_output=True,text=True,timeout=120);out=r.stdout+r.stderr;log=D/f'{mode}-{seed}.log';log.write_text(out);marker=re.search(r'PASS CPU cache[^\n]*',out);first=next((s for s in out.splitlines() if 'mismatch' in s.lower()),'');passed=r.returncode==0 and bool(marker) and f'checks={program["expected_checks"]} ' in marker[0];expected=passed if mode in ('control','packed') else r.returncode!=0 and bool(first)
       row=dict(model=model,mode=mode,seed=seed,pauses=pause,early_done=early,resets=resets,exit_code=r.returncode,expected_verdict=expected,passed=passed,first_mismatch=first,log=str(log),log_sha256=digest(log),metrics={k:int(v) for k,v in re.findall(r'([a-z_]+)=(\d+)',marker[0])} if marker else {})
       runs.append(row);(C/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(model,mode,seed,expected,first,flush=True)
       if not expected:raise RuntimeError(out)
    identities=inputs(P)==record['quartus_sources'] and all(digest(Path(p))==sha for p,sha in protected.items());assert all(digest(Path(x['path']))==x['sha256'] for x in archive)
    report.update(passed=identities and len(runs)==22,input_archive=archive,ghdl_version=subprocess.check_output([find_tool('ghdl'),'--version'],text=True).splitlines()[0],protected_sha256=protected,source_identity_verified=identities,scope='Exact current CPU; authored signed/unsigned DIV HI/LO plus cached branch/IRQ/COP0/coherency; delayed memory, legal CE, warm/partial-fill reset; no GTE command/game/PHY proof')
    (C/'report.json').write_text(json.dumps(report,indent=2)+'\n');assert report['passed']


if __name__=='__main__':
    main()
