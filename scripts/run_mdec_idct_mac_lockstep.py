#!/usr/bin/env python3
"""Check the DSP integration's VHDL wiring against the selected MDEC."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from prepare_mdec_idct_mac import transform, SOURCE
from run_mdec_release_chain import build, UPSTREAM
from run_mdec_idct_pipe import vectors
from tool_paths import ROOT, find_tool

MODEL = '''library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity psx_mdec_idct_mac is
   port(clk,enable,valid,first: in std_logic;
        a0,b0,a1,b1: in std_logic_vector(15 downto 0);
        sum: out std_logic_vector(30 downto 0));
end entity;
architecture model of psx_mdec_idct_mac is
   signal p0,p1: signed(31 downto 0);
   signal acc: signed(30 downto 0);
begin
   sum <= std_logic_vector(acc);
   process(clk) begin
      if rising_edge(clk) and enable='1' then
         p0 <= signed(a0)*signed(b0); p1 <= signed(a1)*signed(b1);
         if valid='1' then
            if first='1' then
               acc <= to_signed(16384,31)+resize(p0,31)+resize(p1,31);
            else
               acc <= acc+resize(p0,31)+resize(p1,31);
            end if;
         end if;
      end if;
   end process;
end architecture;
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(project, output, fault):
    manifest = json.loads((project/'fit-inputs.json').read_text())
    source = [Path(r['path']) for r in manifest['quartus_sources']
              if r['assignment']=='VHDL_FILE' and Path(r['path']).name=='mdec.vhd']
    if len(source)!=1 or sha(source[0])!=SOURCE:
        raise ValueError('Requires exact selected MDEC control')
    if output.exists():
        raise ValueError('Fresh output required')
    lib=Path(find_tool('quartus_sh')).parents[1]/'eda/sim_lib'
    mem=UPSTREAM/'sim/system/src/mem'
    protected = [source[0], project/'fit-inputs.json', Path(__file__),
                 ROOT/'scripts/prepare_mdec_idct_mac.py',ROOT/'scripts/run_mdec_release_chain.py',
                 ROOT/'scripts/run_mdec_idct_pipe.py', ROOT/'scripts/prepare_dpram_sdp.py',
                 ROOT/'sim/reference/tb_mdec_idct_pipe.vhd', ROOT/'sim/reference/tb_mdec_reference.vhd',
                 UPSTREAM/'rtl/dpram.vhd',lib/'altera_mf.vhd',lib/'altera_mf_components.vhd']
    for name in ('dpram.vhd','RamMLAB.vhd','SyncFifo.vhd','SyncFifoFallThrough.vhd'):
        protected.append(mem/name if (mem/name).exists() else UPSTREAM/'rtl'/name)
    ledger={str(p):sha(p) for p in protected}
    build(output,source[0].read_text())
    evidence=output/'source-evidence'
    for p in protected:
        target=evidence/str(p).lstrip('/')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(p.read_bytes())
        if sha(target)!=ledger[str(p)]: raise ValueError('Source drift during capture')
    transformed=transform(source[0].read_text())
    if fault:
        before='a1 => std_logic_vector(idct_calcP_mul21)'
        if transformed.count(before)!=1: raise ValueError('Wiring fault anchor changed')
        transformed=transformed.replace(before,'a1 => std_logic_vector(idct_calcP_mul11)')
    model=output/'mac-model.vhd';model.write_text(MODEL)
    candidate=output/'mdec_pipe.vhd'
    candidate.write_text(re.sub(r'\bmdec\b','mdec_pipe',transformed))
    ghdl=find_tool('ghdl');opts=['--std=08','-frelaxed','-fsynopsys']
    with (output/'integration-compile.log').open('w') as log:
        for path in (model,candidate,ROOT/'sim/reference/tb_mdec_idct_pipe.vhd'):
            subprocess.run([ghdl,'-a',*opts,str(path)],cwd=output,stdout=log,stderr=subprocess.STDOUT,check=True)
        vectors(output/'mdec-random.txt',1)
        subprocess.run([ghdl,'-e',*opts,'tb_mdec_idct_pipe'],cwd=output,stdout=log,stderr=subprocess.STDOUT,check=True)
    results=[]
    for seed in ((1,) if fault else (1,2)):
        v=output/f'vectors-{seed}.txt';vectors(v,seed)
        r=subprocess.run([ghdl,'-r',*opts,'tb_mdec_idct_pipe',f'-gVECTOR_FILE={v}',
                          '--assert-level=error','--ieee-asserts=disable-at-0'],cwd=output,
                         stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=1200)
        log=output/f'seed-{seed}.log';log.write_text(r.stdout)
        passed=(r.returncode!=0 and 'MDEC DSP accumulator differs from original' in r.stdout if fault
                else r.returncode==0 and 'PASS MDEC IDCT pipeline lockstep' in r.stdout)
        results.append(dict(seed=seed,passed=passed,exit_code=r.returncode,log_sha256=sha(log)))
        print(seed,'PASS' if passed else 'FAIL',r.stdout[-500:].strip(),flush=True)
    drift=[p for p,h in ledger.items() if sha(Path(p))!=h]
    report=dict(passed=all(r['passed'] for r in results) and not drift,results=results,
                fault=fault,inputs=ledger,inputs_changed=drift,
                scope='Selected MDEC cycle ports; portable DSP stage model, separately vendor-qualified',
                hardware_verified=False,selected=False)
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['passed'] else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('project',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--fault',action='store_true')
    a=p.parse_args()
    raise SystemExit(run(a.project.resolve(),a.output.resolve(),a.fault))
